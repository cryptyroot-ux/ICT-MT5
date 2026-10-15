"""Causal reference model for the experimental V2 variant.

This module is a research fixture. It is not the MQL5 EA, not a MetaEditor
compile test, and not a substitute for MT5 execution.

Causality contract enforced here:

* Premium/discount uses the last *previous* completed server day, never the
  current day's extremes (the V1/V2 leak that invalidated earlier results).
* Session volatility uses only closed M5 bars that existed before the decision
  bar, limited to the last ``lookback_bars`` of them.
* Order submission is impossible until transaction costs are verified and
  observe-only is disabled.

Timestamps are broker/server wall-clock. Every bar carries its server day and
hour explicitly, so the model never depends on the host machine timezone.
"""

from __future__ import annotations

import argparse
import bisect
import calendar
import csv
import datetime
import hashlib
import json
import math
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from research.smc_core import (
    Bar,
    Direction,
    FairValueGap,
    LiquiditySweep,
    StructureBreak,
    atr_sma,
    confirmed_pivots,
    detect_bos_sequence,
    midpoint,
    qualifies_displacement,
    valid_limit_entry,
)

M5_SECONDS = 300


@dataclass(frozen=True)
class ServerBar:
    """One completed M5 bar with its broker/server calendar context."""

    time: int
    server_day: datetime.date
    server_hour: int
    open: float
    high: float
    low: float
    close: float
    spread_price: float
    complete: bool = True

    def __post_init__(self) -> None:
        values = (self.open, self.high, self.low, self.close, self.spread_price)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("OHLC and spread must be finite")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("OHLC bounds are inconsistent")
        if self.high < self.low:
            raise ValueError("bar high must be >= bar low")
        if self.spread_price < 0:
            raise ValueError("spread must be non-negative")

    def to_core_bar(self) -> Bar:
        """Return the timezone-free ``research.smc_core.Bar`` view."""
        return Bar(
            time=self.time,
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            complete=self.complete,
        )


@dataclass(frozen=True)
class DailyRange:
    """Server-day high/low built only from completed bars."""

    day: datetime.date
    high: float
    low: float


@dataclass(frozen=True)
class V2Config:
    """Frozen V2 constants. These are not an optimisation grid."""

    swing_span: int = 2
    max_liquidity_age_bars: int = 100
    sweep_threshold_ticks: float = 2.0
    equal_level_ticks: float = 2.0
    max_reclaim_bars: int = 3
    setup_development_bars: int = 12
    atr_period: int = 14
    displacement_atr: float = 1.0
    displacement_body_ratio: float = 0.60
    displacement_close_quartile: float = 0.25
    min_sl_price: float = 20.0
    atr_sl_multiplier: float = 3.0
    target_r: float = 3.0
    session_lookback_bars: int = 200
    session_ratio: float = 1.30
    pending_expiry_bars: int = 6
    pending_expiry_seconds: int = 1800
    max_spread_price: float = 0.50
    risk_percent: float = 0.25
    daily_loss_percent: float = 2.0
    observe_only: bool = True


def may_submit_order(cost_verified: bool, observe_only: bool) -> bool:
    """True only when costs are verified and the EA is not observe-only."""
    return bool(cost_verified) and not bool(observe_only)


def load_v2_bars(
    path: Path,
    start_time: datetime.datetime | None = None,
    end_time: datetime.datetime | None = None,
    point_size: float = 0.01,
) -> list[ServerBar]:
    """Load M5 rows as server bars, never reading past ``end_time``.

    ``point_size`` converts the export's ``spread_points`` column to a price
    distance (XAUUSD on HFM Demo4 quotes to 0.01).
    """
    if point_size <= 0 or not math.isfinite(point_size):
        raise ValueError("point_size must be positive and finite")
    bars: list[ServerBar] = []
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            stamp = datetime.datetime.strptime(row["datetime"], "%Y.%m.%d %H:%M:%S")
            if end_time is not None and stamp > end_time:
                break  # Do not even load the burned OOS observations.
            if start_time is not None and stamp < start_time:
                continue
            bars.append(
                ServerBar(
                    time=calendar.timegm(stamp.timetuple()),
                    server_day=stamp.date(),
                    server_hour=stamp.hour,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    spread_price=float(row["spread_points"]) * point_size,
                    complete=True,
                )
            )
    if not bars:
        raise ValueError(f"{path}: no M5 rows")
    for left, right in zip(bars, bars[1:]):
        if right.time <= left.time:
            raise ValueError(f"{path}: timestamps must be strictly increasing")
    return bars


def aggregate_daily_ranges(bars: Sequence[ServerBar]) -> list[DailyRange]:
    """Aggregate completed bars into ordered server-day ranges."""
    grouped: dict[datetime.date, list[ServerBar]] = {}
    for bar in bars:
        if bar.complete:
            grouped.setdefault(bar.server_day, []).append(bar)
    return [
        DailyRange(day, max(bar.high for bar in rows), min(bar.low for bar in rows))
        for day, rows in sorted(grouped.items())
    ]


def prior_daily_range(
    daily: Sequence[DailyRange], decision_day: datetime.date
) -> DailyRange | None:
    """Return the most recent range strictly earlier than ``decision_day``."""
    candidates = [entry for entry in daily if entry.day < decision_day]
    return candidates[-1] if candidates else None


def premium_discount_allows(
    direction: Direction, decision_price: float, prior_range: DailyRange | None
) -> bool:
    """BUY only below the prior day's midpoint, SELL only above it."""
    if prior_range is None or prior_range.high <= prior_range.low:
        return False
    if not math.isfinite(decision_price):
        return False
    midpoint = (prior_range.high + prior_range.low) / 2.0
    if direction is Direction.BUY:
        return decision_price < midpoint
    return decision_price > midpoint


def rolling_session_allowed(
    bars: Sequence[ServerBar],
    decision_index: int,
    lookback_bars: int = 200,
    ratio: float = 1.30,
) -> bool:
    """Mirror the MQL session filter over the last ``lookback_bars`` closed bars.

    Only bars strictly before ``decision_index`` are visible, matching
    ``CopyRates(symbol, PERIOD_M5, 1, 200, rates)``. A short window is a
    rejection, never a silent "allow all".
    """
    if lookback_bars <= 0 or ratio <= 0:
        raise ValueError("lookback_bars and ratio must be positive")
    if decision_index <= 0 or decision_index > len(bars):
        raise ValueError("decision_index must address an existing bar")
    window = [bar for bar in bars[:decision_index] if bar.complete][-lookback_bars:]
    if len(window) < lookback_bars:
        return False

    decision_hour = bars[decision_index].server_hour
    ranges_by_hour: dict[int, list[float]] = {}
    for bar in window:
        ranges_by_hour.setdefault(bar.server_hour, []).append(bar.high - bar.low)
    hour_means = {
        hour: sum(values) / len(values) for hour, values in ranges_by_hour.items()
    }
    if not hour_means or decision_hour not in hour_means:
        return False
    mean = sum(hour_means.values()) / len(hour_means)
    return hour_means[decision_hour] >= mean * ratio


@dataclass(frozen=True)
class PendingFill:
    """A limit order that filled, with the window it was allowed to see."""

    entry_index: int
    entry_price: float
    decision_index: int
    expiry_time: int


@dataclass(frozen=True)
class V2Setup:
    """Causal facts of one FVG-confirmed V2 order candidate.

    Prices are not stored here: the limit is the gap midpoint and the
    protective levels come from :func:`protection_levels` so that widening can
    never be undone downstream.
    """

    c3_index: int
    order_decision_index: int
    direction: Direction
    gap: FairValueGap
    sweep: LiquiditySweep
    atr: float

    @property
    def limit_price(self) -> float:
        return midpoint(self.gap.lower, self.gap.upper)


@dataclass
class V2Report:
    """Diagnostics for one V2 pass. Never an acceptance result."""

    model_kind: str = "EXPLORATORY_BAR_MODEL"
    oos_claim_permitted: bool = False
    candidates: list[V2Setup] = field(default_factory=list)
    rejections: Counter = field(default_factory=Counter)


def _as_core(bars: Sequence) -> Sequence[Bar]:
    """Accept either ``ServerBar`` or ``Bar`` sequences without re-copying.

    A ``Bar`` sequence is returned as-is: callers convert once and reuse the
    result, because converting a multi-year series per candidate is quadratic.
    """
    if not bars:
        return []
    if isinstance(bars[0], Bar):
        return bars
    return [
        bar.to_core_bar() if isinstance(bar, ServerBar) else bar for bar in bars
    ]


def _is_pivot_high(core: Sequence[Bar], index: int, span: int) -> bool:
    if index - span < 0 or index + span >= len(core):
        return False
    centre = core[index]
    if not centre.complete:
        return False
    return all(
        core[index - offset].high < centre.high and core[index + offset].high < centre.high
        for offset in range(1, span + 1)
    )


def _is_pivot_low(core: Sequence[Bar], index: int, span: int) -> bool:
    if index - span < 0 or index + span >= len(core):
        return False
    centre = core[index]
    if not centre.complete:
        return False
    return all(
        core[index - offset].low > centre.low and core[index + offset].low > centre.low
        for offset in range(1, span + 1)
    )


def round_to_tick(value: float, tick_size: float) -> float:
    """Round to the nearest tick, the same rule the MQL side must use."""
    if tick_size <= 0 or not math.isfinite(tick_size) or not math.isfinite(value):
        raise ValueError("value and tick_size must be finite and tick_size positive")
    return round(value / tick_size) * tick_size


def pending_limit_fill(
    bars: Sequence[ServerBar],
    direction: Direction,
    limit_price: float,
    submit_index: int,
    pending_expiry_bars: int = 6,
    pending_expiry_seconds: int = 1800,
) -> PendingFill | None:
    """Fill a V2 midpoint limit using Bid/Ask touches after submission.

    A BUY limit fills when ``low + spread <= limit`` (the Ask traded down to the
    limit); a SELL limit fills when ``high >= limit`` (the Bid traded up to the
    limit). The submission bar itself is already closed at decision time, so the
    scan starts at ``submit_index + 1``.
    """
    if direction not in (Direction.BUY, Direction.SELL):
        raise ValueError("direction must be BUY or SELL")
    if not math.isfinite(limit_price):
        raise ValueError("limit_price must be finite")
    if submit_index < 0 or submit_index >= len(bars):
        raise ValueError("submit_index must address an existing bar")
    if pending_expiry_bars <= 0 or pending_expiry_seconds <= 0:
        raise ValueError("pending expiry limits must be positive")
    expiry_time = bars[submit_index].time + min(
        pending_expiry_bars * M5_SECONDS, pending_expiry_seconds
    )
    for index in range(submit_index + 1, len(bars)):
        bar = bars[index]
        if bar.time >= expiry_time:
            break
        if not bar.complete:
            continue
        touched = (
            bar.low + bar.spread_price <= limit_price
            if direction is Direction.BUY
            else bar.high >= limit_price
        )
        if touched:
            return PendingFill(index, limit_price, submit_index, expiry_time)
    return None


def protection_levels(
    direction: Direction,
    entry: float,
    sweep_extreme: float,
    atr: float,
    tick_size: float,
    min_sl_price: float = 20.0,
    atr_multiplier: float = 3.0,
    target_r: float = 3.0,
) -> tuple[float, float]:
    """Return the final (stop_loss, take_profit) for a V2 order.

    The raw stop sits half an ATR beyond the sweep extreme, then the distance is
    widened (never tightened) to at least ``min_sl_price`` and at least
    ``atr_multiplier`` ATR. A SELL stop therefore sits *above* the sweep high.
    The target is a fixed ``target_r`` of that final distance.
    """
    if direction not in (Direction.BUY, Direction.SELL):
        raise ValueError("direction must be BUY or SELL")
    values = (entry, sweep_extreme, atr, tick_size, min_sl_price, atr_multiplier, target_r)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("protection inputs must be finite")
    if entry <= 0 or atr <= 0 or tick_size <= 0:
        raise ValueError("entry, atr, and tick_size must be positive")
    if min_sl_price <= 0 or atr_multiplier <= 0 or target_r <= 0:
        raise ValueError("min_sl_price, atr_multiplier, and target_r must be positive")

    raw_stop = (
        sweep_extreme - 0.5 * atr
        if direction is Direction.BUY
        else sweep_extreme + 0.5 * atr
    )
    raw_distance = abs(entry - raw_stop)
    distance = max(raw_distance, min_sl_price, atr_multiplier * atr)
    stop = entry - distance if direction is Direction.BUY else entry + distance
    target = entry + target_r * distance if direction is Direction.BUY else entry - target_r * distance
    return round_to_tick(stop, tick_size), round_to_tick(target, tick_size)


def bos_close_confirmation(bos: StructureBreak, timeframe_seconds: int = 3600) -> StructureBreak:
    """Re-base a shared-core BOS onto the MQL convention: confirmation = bar close.

    ``research.smc_core.detect_bos_sequence`` records the *opening* timestamp of
    the confirming bar. The EA records that bar's close. For H1 that is a full
    hour, which is enough to reject a genuinely fresh setup, so V2 corrects it
    here instead of touching the frozen B3 core.
    """
    if timeframe_seconds <= 0:
        raise ValueError("timeframe_seconds must be positive")
    return StructureBreak(
        direction=bos.direction,
        level=bos.level,
        pivot_time=bos.pivot_time,
        confirmation_time=bos.confirmation_time + timeframe_seconds,
    )


def latest_bos_before(
    bos_events: Sequence[StructureBreak], confirmation_time: int
) -> StructureBreak | None:
    """Last H1 close-confirmed break at or before ``confirmation_time``."""
    latest: StructureBreak | None = None
    for bos in bos_events:
        if bos.confirmation_time > confirmation_time:
            continue
        if latest is None or bos.confirmation_time > latest.confirmation_time:
            latest = bos
    return latest


def within_development_window(
    bos: StructureBreak, sweep: LiquiditySweep, config: V2Config
) -> bool:
    """The bias must be confirmed first and inside the frozen 12 M5 bar window."""
    if bos.confirmation_time > sweep.confirmation_time:
        return False
    age_seconds = sweep.confirmation_time - bos.confirmation_time
    return age_seconds <= config.setup_development_bars * M5_SECONDS


def enumerate_causal_sweeps(
    m5: Sequence,
    config: V2Config,
    tick_size: float,
    direction: Direction | None = None,
) -> list[LiquiditySweep]:
    """Enumerate every causal M5 liquidity sweep, oldest confirmation first.

    Unlike the EA, which picks only the newest sweep inside its live window,
    research needs the full causal set. Each sweep is emitted only after its
    reclaim bar closed, so no later bar can change it. When two different pool
    levels are reclaimed in the same bar the outcome is structurally ambiguous
    and every sweep for that bar is rejected, matching the EA.
    """
    if tick_size <= 0 or not math.isfinite(tick_size):
        raise ValueError("tick_size must be positive and finite")
    directions = (
        [direction]
        if direction is not None
        else [Direction.BUY, Direction.SELL]
    )
    core = _as_core(m5)
    collected: list[tuple[LiquiditySweep, int]] = []
    for one_direction in directions:
        if one_direction not in (Direction.BUY, Direction.SELL):
            raise ValueError("direction must be BUY, SELL, or None")
        collected.extend(_sweeps_for_direction(core, config, tick_size, one_direction))

    equal_level_distance = config.equal_level_ticks * tick_size
    grouped: dict[tuple[Direction, int], list[tuple[LiquiditySweep, int]]] = {}
    for sweep, pivot_time in collected:
        grouped.setdefault((sweep.direction, sweep.confirmation_time), []).append(
            (sweep, pivot_time)
        )

    result: list[LiquiditySweep] = []
    for (one_direction, _time), group in grouped.items():
        levels = [sweep.level for sweep, _ in group]
        if max(levels) - min(levels) > equal_level_distance:
            continue  # Structurally ambiguous: the EA rejects these too.
        # Equal pools are one liquidity level; the newest confirmed pivot anchors it.
        result.append(max(group, key=lambda item: item[1])[0])
    result.sort(key=lambda sweep: (sweep.confirmation_time, sweep.excursion_time, sweep.level))
    return result


def _sweeps_for_direction(
    core: Sequence[Bar], config: V2Config, tick_size: float, direction: Direction
) -> list[tuple[LiquiditySweep, int]]:
    if len(core) < config.swing_span * 2 + 2:
        return []
    highs, lows = confirmed_pivots(core, config.swing_span)
    pivots = lows if direction is Direction.BUY else highs
    threshold = config.sweep_threshold_ticks * tick_size
    last_index = len(core) - 1
    results: list[tuple[LiquiditySweep, int]] = []

    for pivot in pivots:
        level = pivot.price
        best: LiquiditySweep | None = None
        first_sweep = pivot.confirmation_index + 1
        last_sweep = min(last_index, pivot.confirmation_index + config.max_liquidity_age_bars)
        for sweep_index in range(first_sweep, last_sweep + 1):
            bar = core[sweep_index]
            if bar.time < pivot.confirmation_time:
                continue
            if direction is Direction.BUY:
                if bar.low > level - threshold:
                    continue
            elif bar.high < level + threshold:
                continue
            extreme = bar.low if direction is Direction.BUY else bar.high
            reclaim_end = min(last_index, sweep_index + config.max_reclaim_bars)
            for index in range(sweep_index, reclaim_end + 1):
                candidate = core[index]
                extreme = (
                    min(extreme, candidate.low)
                    if direction is Direction.BUY
                    else max(extreme, candidate.high)
                )
                reclaimed = (
                    candidate.close > level
                    if direction is Direction.BUY
                    else candidate.close < level
                )
                if not reclaimed:
                    continue
                confirmation_time = candidate.time + M5_SECONDS
                if best is None or confirmation_time > best.confirmation_time:
                    best = LiquiditySweep(
                        direction,
                        level,
                        extreme,
                        bar.time,
                        confirmation_time,
                        f"{direction.value}|{level:.10f}|{bar.time}",
                    )
                break
        if best is not None:
            results.append((best, pivot.confirmation_time))
    return results


def _atr_at(core: Sequence[Bar], index: int, period: int) -> float | None:
    """SMA true range of the ``period`` bars ending at ``index``, inclusive.

    Mirrors ``CSMCDisplacement::ATRAt`` without slicing the whole series.
    """
    if period < 1 or index - period + 1 < 0:
        return None
    total = 0.0
    for current in range(index - period + 1, index + 1):
        candle = core[current]
        previous_close = core[current - 1].close if current > 0 else candle.close
        total += max(
            candle.high - candle.low,
            abs(candle.high - previous_close),
            abs(candle.low - previous_close),
        )
    return total / period


def find_post_sweep_displacement(
    m5: Sequence,
    sweep: LiquiditySweep,
    config: V2Config,
    times: Sequence[int] | None = None,
) -> tuple[int, float] | None:
    """Return the displacement bar index and its ATR, mirroring ``SMC_Displacement``.

    The bar must close beyond an internal pivot that itself formed after the
    sweep reclaim, and it must also satisfy the frozen displacement thresholds.
    """
    core = _as_core(m5)
    span = config.swing_span
    timeout = sweep.confirmation_time + config.setup_development_bars * M5_SECONDS
    internal_level = 0.0
    internal_confirm = 0

    # The event window starts at the reclaim, so seek there instead of walking
    # the whole history for every sweep.
    if times is None:
        times = [bar.time for bar in core]
    first = max(
        config.atr_period + span * 2,
        bisect.bisect_left(times, sweep.confirmation_time),
    )
    for index in range(first, len(core)):
        bar = core[index]
        if bar.time < sweep.confirmation_time:
            continue
        if bar.time + M5_SECONDS > timeout:
            break
        broke_internal = (
            internal_level > 0.0
            and internal_confirm <= bar.time
            and (
                bar.close > internal_level
                if sweep.direction is Direction.BUY
                else bar.close < internal_level
            )
        )
        if broke_internal:
            atr = _atr_at(core, index, config.atr_period)
            if atr and qualifies_displacement(bar, atr, sweep.direction):
                return index, atr
        pivot_index = index - span
        if pivot_index < span or pivot_index + span > index:
            continue
        if core[pivot_index].time < sweep.confirmation_time:
            continue
        if sweep.direction is Direction.BUY and _is_pivot_high(core, pivot_index, span):
            internal_level = core[pivot_index].high
            internal_confirm = bar.time + M5_SECONDS
        elif sweep.direction is Direction.SELL and _is_pivot_low(core, pivot_index, span):
            internal_level = core[pivot_index].low
            internal_confirm = bar.time + M5_SECONDS
    return None


def find_associated_fvg(
    m5: Sequence, sweep: LiquiditySweep, displacement_index: int, tick_size: float
) -> tuple[FairValueGap, int] | None:
    """Return the three-candle FVG around the displacement and its C3 index."""
    if tick_size <= 0 or not math.isfinite(tick_size):
        raise ValueError("tick_size must be positive and finite")
    core = _as_core(m5)
    if not 0 < displacement_index < len(core) - 1:
        return None
    c1 = core[displacement_index - 1]
    c2 = core[displacement_index]
    c3 = core[displacement_index + 1]
    if c1.time < sweep.confirmation_time or c2.time < sweep.confirmation_time:
        return None
    if c3.time < c2.time + M5_SECONDS:
        return None
    if sweep.direction is Direction.BUY:
        lower, upper = c1.high, c3.low
    else:
        lower, upper = c3.high, c1.low
    if upper <= lower or upper - lower < tick_size:
        return None
    gap = FairValueGap(
        sweep.direction, lower, upper, c1.time, c2.time, c3.time + M5_SECONDS
    )
    return gap, displacement_index + 1


def build_v2_candidates(
    m5: Sequence[ServerBar],
    h1: Sequence,
    config: V2Config,
    tick_size: float = 0.01,
) -> V2Report:
    """Emit only causally confirmed V2 order candidates plus rejection counts."""
    report = V2Report()
    m5_core = _as_core(m5)
    h1_core = _as_core(h1)
    m5_times = [bar.time for bar in m5_core]
    daily_ranges = aggregate_daily_ranges(m5)
    bos_events = [
        bos_close_confirmation(bos) for bos in detect_bos_sequence(h1_core, span=config.swing_span)
    ]

    for sweep in enumerate_causal_sweeps(m5_core, config, tick_size):
        bos = latest_bos_before(bos_events, sweep.confirmation_time)
        if bos is None or bos.direction is not sweep.direction:
            report.rejections["no_matching_confirmed_h1_bos"] += 1
            continue
        if not within_development_window(bos, sweep, config):
            report.rejections["bos_or_sweep_stale"] += 1
            continue
        displacement = find_post_sweep_displacement(m5_core, sweep, config, m5_times)
        if displacement is None:
            report.rejections["no_post_sweep_displacement_choch"] += 1
            continue
        displacement_index, atr_value = displacement
        fvg_result = find_associated_fvg(m5_core, sweep, displacement_index, tick_size)
        if fvg_result is None:
            report.rejections["no_associated_three_candle_fvg"] += 1
            continue
        gap, c3_index = fvg_result

        order_index = c3_index + 1
        if order_index >= len(m5):
            report.rejections["no_post_c3_decision_bar"] += 1
            continue
        decision_bar = m5[order_index]
        bid = decision_bar.open
        ask = bid + decision_bar.spread_price
        previous_day = prior_daily_range(daily_ranges, decision_bar.server_day)
        if not premium_discount_allows(sweep.direction, bid, previous_day):
            report.rejections["v2_wrong_previous_d1_premium_discount"] += 1
            continue
        if not rolling_session_allowed(
            m5, order_index, config.session_lookback_bars, config.session_ratio
        ):
            report.rejections["v2_outside_rolling_high_conviction_session"] += 1
            continue
        limit_price = midpoint(gap.lower, gap.upper)
        if not valid_limit_entry(sweep.direction, limit_price, bid, ask):
            report.rejections["limit_already_crossed_at_submission"] += 1
            continue
        report.candidates.append(
            V2Setup(c3_index, order_index, sweep.direction, gap, sweep, atr_value)
        )
    return report

@dataclass(frozen=True)
class CostModel:
    """Broker cost assumptions. ``verified=False`` keeps them labelled as guesses."""

    tick_size: float = 0.01
    commission_per_lot: float = 0.0
    slippage_per_lot: float = 10.0
    contract_size: float = 100.0
    verified: bool = False

    def __post_init__(self) -> None:
        if self.contract_size <= 0 or self.tick_size <= 0:
            raise ValueError("contract_size and tick_size must be positive")
        if self.commission_per_lot < 0 or self.slippage_per_lot < 0:
            raise ValueError("cost assumptions must be non-negative")

    @property
    def slippage_price(self) -> float:
        return self.slippage_per_lot / self.contract_size

    @property
    def commission_price(self) -> float:
        return self.commission_per_lot / self.contract_size

    def status(self) -> str:
        return "VERIFIED" if self.verified else "UNVERIFIED_ASSUMPTION"


@dataclass(frozen=True)
class Trade:
    """One filled and closed V2 trade, priced with the Bid/Ask bar model."""

    setup_id: str
    direction: Direction
    entry_index: int
    entry_time: int
    entry_price: float
    stop_loss: float
    take_profit: float
    distance: float
    exit_index: int | None
    exit_time: int | None
    exit_price: float
    exit_reason: str
    gross_r: float
    spread_price: float
    slippage_price: float
    commission_price: float

    def net_r(self, costs: CostModel) -> float:
        """Gross R minus slippage and commission.

        The spread is already inside the fill and exit prices: a BUY limit only
        fills when the Ask trades down to it, and long exits trigger on the Bid.
        """
        if self.distance <= 0:
            raise ValueError("trade distance must be positive")
        extra = costs.slippage_price + costs.commission_price
        return self.gross_r - (extra / self.distance)


def simulate_v2_pending(
    candidates: Sequence[V2Setup],
    m5: Sequence[ServerBar],
    config: V2Config,
    costs: CostModel,
) -> list[Trade]:
    """Model the pending midpoint limit and its protective exit on closed bars.

    Exits use Bid for long positions and Ask for short ones, mirroring how the
    broker closes them. A bar that touches both levels resolves to the stop
    first, because the order of intrabar ticks is unknowable from bars.
    """
    trades: list[Trade] = []
    tick_size = costs.tick_size
    for candidate in candidates:
        fill = pending_limit_fill(
            m5,
            candidate.direction,
            candidate.limit_price,
            submit_index=candidate.order_decision_index,
            pending_expiry_bars=config.pending_expiry_bars,
            pending_expiry_seconds=config.pending_expiry_seconds,
        )
        if fill is None:
            continue
        stop, target = protection_levels(
            candidate.direction,
            candidate.limit_price,
            candidate.sweep.extreme,
            candidate.atr,
            tick_size,
            min_sl_price=config.min_sl_price,
            atr_multiplier=config.atr_sl_multiplier,
            target_r=config.target_r,
        )
        distance = abs(candidate.limit_price - stop)
        if distance <= 0:
            continue
        is_buy = candidate.direction is Direction.BUY
        exit_index = None
        exit_price = 0.0
        exit_reason = "data_end"
        for index in range(fill.entry_index, len(m5)):
            bar = m5[index]
            if not bar.complete:
                continue
            if is_buy:
                bid_high, bid_low = bar.high, bar.low
                stop_hit = bid_low - costs.slippage_price <= stop
                target_hit = bid_high - costs.slippage_price >= target
            else:
                ask_high, ask_low = bar.high + bar.spread_price, bar.low + bar.spread_price
                stop_hit = ask_high + costs.slippage_price >= stop
                target_hit = ask_low + costs.slippage_price <= target
            if stop_hit:
                exit_index, exit_price, exit_reason = index, stop, "stop_loss"
                break
            if target_hit:
                exit_index, exit_price, exit_reason = index, target, "take_profit"
                break
        if exit_index is None:
            exit_index = len(m5) - 1
            exit_price = m5[exit_index].close
        move = exit_price - fill.entry_price
        gross_r = move / distance if is_buy else -move / distance
        trades.append(
            Trade(
                setup_id=candidate.sweep.setup_id,
                direction=candidate.direction,
                entry_index=fill.entry_index,
                entry_time=m5[fill.entry_index].time,
                entry_price=fill.entry_price,
                stop_loss=stop,
                take_profit=target,
                distance=distance,
                exit_index=exit_index,
                exit_time=m5[exit_index].time,
                exit_price=exit_price,
                exit_reason=exit_reason,
                gross_r=gross_r,
                spread_price=m5[fill.entry_index].spread_price,
                slippage_price=costs.slippage_price,
                commission_price=costs.commission_price,
            )
        )
    trades.sort(key=lambda trade: (trade.entry_time, trade.entry_index))
    return trades


def summarise_trades(trades: Sequence[Trade], costs: CostModel) -> dict:
    """Aggregate net R. These numbers are diagnostic, never an acceptance result."""
    results = [trade.net_r(costs) for trade in trades]
    wins = [value for value in results if value > 0]
    losses = [value for value in results if value <= 0]
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    equity = 0.0
    peak = 0.0
    drawdown = 0.0
    for value in results:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return {
        "trades": len(results),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": (len(wins) / len(results)) if results else 0.0,
        "expectancy_r": (sum(results) / len(results)) if results else 0.0,
        "total_r": sum(results),
        "profit_factor": (
            gross_win / gross_loss if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0)
        ),
        "max_drawdown_r": drawdown,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stamp(value: datetime.datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def run_v2_exploratory(
    m5_path: Path,
    h1_path: Path,
    export_manifest_path: Path,
    config: V2Config,
    dev_start: datetime.datetime,
    dev_end: datetime.datetime,
    output_path: Path,
    costs: CostModel | None = None,
) -> dict:
    """Run the V2 bar model over the DEV window and write an evidence report.

    The interval after ``dev_end`` is never read, so the burned OOS cannot leak
    into this diagnostic. Acceptance stays INCONCLUSIVE by construction.
    """
    if dev_end <= dev_start:
        raise ValueError("dev_end must be after dev_start")
    costs = costs or CostModel()
    m5 = load_v2_bars(Path(m5_path), start_time=dev_start, end_time=dev_end)
    h1 = load_v2_bars(Path(h1_path), start_time=dev_start, end_time=dev_end)
    report = build_v2_candidates(m5, h1, config, tick_size=costs.tick_size)
    trades = simulate_v2_pending(report.candidates, m5, config, costs)
    metrics = summarise_trades(trades, costs)
    stress = summarise_trades(
        [
            Trade(**{**vars(trade), "slippage_price": 2 * trade.slippage_price})
            for trade in trades
        ],
        CostModel(
            tick_size=costs.tick_size,
            commission_per_lot=2 * costs.commission_per_lot,
            slippage_per_lot=2 * costs.slippage_per_lot,
            contract_size=costs.contract_size,
            verified=costs.verified,
        ),
    )
    payload = {
        "model_kind": "EXPLORATORY_BAR_MODEL",
        "oos_claim_permitted": False,
        "acceptance_status": "INCONCLUSIVE",
        "acceptance_note": (
            "Diagnostic DEV run on selection-affected data. No untouched OOS remains, "
            "so the frozen B3 gates cannot be satisfied by this report."
        ),
        "strategy": "SMC-H1M5-SweepFVG",
        "variant": "V2_PARITY",
        "window": {
            "dev_start": _stamp(dev_start),
            "dev_end": _stamp(dev_end),
            "burned_oos_excluded": True,
            "burned_oos_interval": "2024-04-23..2026-10-15 (previously viewed)",
        },
        "data": {
            "m5_rows_loaded": len(m5),
            "h1_rows_loaded": len(h1),
            "first_bar": _stamp(datetime.datetime.fromtimestamp(m5[0].time, datetime.UTC).replace(tzinfo=None)),
            "last_bar": _stamp(datetime.datetime.fromtimestamp(m5[-1].time, datetime.UTC).replace(tzinfo=None)),
        },
        "raw_inputs": {
            "m5_path": str(m5_path),
            "m5_sha256": _sha256(m5_path),
            "h1_path": str(h1_path),
            "h1_sha256": _sha256(h1_path),
            "export_manifest_path": str(export_manifest_path),
            "export_manifest_sha256": _sha256(export_manifest_path),
        },
        "config": {
            key: getattr(config, key) for key in (
                "swing_span", "max_liquidity_age_bars", "sweep_threshold_ticks",
                "equal_level_ticks", "max_reclaim_bars", "setup_development_bars",
                "atr_period", "displacement_atr", "displacement_body_ratio",
                "displacement_close_quartile", "min_sl_price", "atr_sl_multiplier",
                "target_r", "session_lookback_bars", "session_ratio",
                "pending_expiry_bars", "pending_expiry_seconds", "max_spread_price",
                "risk_percent", "daily_loss_percent", "observe_only",
            )
        },
        "cost_assumptions": {
            "commission_status": "VERIFIED" if costs.verified else "UNVERIFIED",
            "commission_per_lot": costs.commission_per_lot,
            "slippage_per_lot": costs.slippage_per_lot,
            "contract_size": costs.contract_size,
            "tick_size": costs.tick_size,
            "note": (
                "Spread comes from each bar's spread_points. Commission and slippage "
                "are assumptions until the broker schedule is verified."
            ),
        },
        "candidates": len(report.candidates),
        "rejections": dict(report.rejections),
        "metrics": metrics,
        "cost_stress_2x": stress,
        "trades": [
            {
                "setup_id": trade.setup_id,
                "direction": trade.direction.value,
                "entry_time": trade.entry_time,
                "entry_price": round(trade.entry_price, 5),
                "stop_loss": round(trade.stop_loss, 5),
                "take_profit": round(trade.take_profit, 5),
                "exit_time": trade.exit_time,
                "exit_price": round(trade.exit_price, 5),
                "exit_reason": trade.exit_reason,
                "gross_r": round(trade.gross_r, 6),
                "net_r": round(trade.net_r(costs), 6),
            }
            for trade in trades
        ],
    }
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return payload


def main(argv: Sequence[str] | None = None) -> dict:
    """CLI entry point: ``python3 -m research.v2_parity --m5-path ... --output ...``."""
    parser = argparse.ArgumentParser(description="V2 exploratory bar-model runner")
    parser.add_argument("--m5-path", required=True)
    parser.add_argument("--h1-path", required=True)
    parser.add_argument("--export-manifest", required=True)
    parser.add_argument("--dev-start", required=True)
    parser.add_argument("--dev-end", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--commission-per-lot", type=float, default=0.0)
    parser.add_argument("--slippage-per-lot", type=float, default=10.0)
    parser.add_argument("--contract-size", type=float, default=100.0)
    args = parser.parse_args(argv)
    costs = CostModel(
        commission_per_lot=args.commission_per_lot,
        slippage_per_lot=args.slippage_per_lot,
        contract_size=args.contract_size,
        verified=False,
    )
    return run_v2_exploratory(
        Path(args.m5_path),
        Path(args.h1_path),
        Path(args.export_manifest),
        V2Config(),
        datetime.datetime.strptime(args.dev_start, "%Y-%m-%d %H:%M:%S"),
        datetime.datetime.strptime(args.dev_end, "%Y-%m-%d %H:%M:%S"),
        Path(args.output),
        costs,
    )


if __name__ == "__main__":
    main()
