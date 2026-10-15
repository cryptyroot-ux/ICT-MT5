"""Deterministic, broker-independent reference rules for SMC-H1M5-SweepFVG.

This module is a logic fixture and input to the optional research runner. It is
not the MQL5 EA, a MetaEditor compile test, or a substitute for MT5 execution.
All timestamps are caller-supplied monotonic epoch seconds (or another monotonic
integer unit); the bars in a sequence are chronological, oldest first.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR
from enum import Enum
import math
from typing import Iterable, Sequence


class Direction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class SetupState(str, Enum):
    WAIT_BIAS = "WAIT_BIAS"
    WAIT_SWEEP = "WAIT_SWEEP"
    WAIT_DISPLACEMENT_CHOCH = "WAIT_DISPLACEMENT_CHOCH"
    WAIT_FVG = "WAIT_FVG"
    WAIT_RETRACE_OR_ORDER = "WAIT_RETRACE_OR_ORDER"
    ORDER_PENDING = "ORDER_PENDING"
    IN_POSITION = "IN_POSITION"
    COMPLETED = "COMPLETED"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"


class FVGStatus(str, Enum):
    FRESH = "FRESH"
    PARTIALLY_MITIGATED = "PARTIALLY_MITIGATED"
    CONSUMED = "CONSUMED"
    INVALIDATED = "INVALIDATED"


class InvalidTransition(ValueError):
    """Raised when a setup tries to skip, repeat, or leave a terminal state."""


@dataclass(frozen=True)
class Bar:
    time: int
    open: float
    high: float
    low: float
    close: float
    complete: bool = True

    def __post_init__(self) -> None:
        values = (self.open, self.high, self.low, self.close)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("OHLC values must be finite")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("OHLC bounds are inconsistent")
        if self.high < self.low:
            raise ValueError("bar high must be >= bar low")


@dataclass(frozen=True)
class Pivot:
    index: int
    time: int
    price: float
    kind: str
    confirmation_index: int
    confirmation_time: int


@dataclass(frozen=True)
class StructureBreak:
    direction: Direction
    level: float
    pivot_time: int
    confirmation_time: int


@dataclass(frozen=True)
class LiquiditySweep:
    direction: Direction
    level: float
    extreme: float
    excursion_time: int
    confirmation_time: int
    setup_id: str


@dataclass(frozen=True)
class FairValueGap:
    direction: Direction
    lower: float
    upper: float
    c1_time: int
    displacement_time: int
    created_time: int


@dataclass(frozen=True)
class TradeDeal:
    position_id: int
    magic: int
    time: int
    profit: float
    commission: float
    swap: float
    fee: float


@dataclass(frozen=True)
class _ActiveSweep:
    index: int
    time: int
    extreme: float


def _closed_indices(bars: Sequence[Bar]) -> list[int]:
    return [index for index, candle in enumerate(bars) if candle.complete]


def confirmed_pivots(
    bars: Sequence[Bar], span: int = 2
) -> tuple[list[Pivot], list[Pivot]]:
    """Return strict completed swing highs/lows with their confirmation time."""
    if span < 1:
        raise ValueError("span must be positive")
    highs: list[Pivot] = []
    lows: list[Pivot] = []
    for index in range(span, len(bars) - span):
        window = bars[index - span : index + span + 1]
        if not all(candle.complete for candle in window):
            continue
        center = bars[index]
        left_right = window[:span] + window[span + 1 :]
        if all(center.high > candle.high for candle in left_right):
            confirm_index = index + span
            highs.append(
                Pivot(index, center.time, center.high, "high", confirm_index, bars[confirm_index].time)
            )
        if all(center.low < candle.low for candle in left_right):
            confirm_index = index + span
            lows.append(
                Pivot(index, center.time, center.low, "low", confirm_index, bars[confirm_index].time)
            )
    return highs, lows


def detect_bos(bars: Sequence[Bar], span: int = 2) -> StructureBreak | None:
    """Return the latest unique close-only break of an already-confirmed pivot."""
    highs, lows = confirmed_pivots(bars, span)
    high_by_confirmation: dict[int, list[Pivot]] = {}
    low_by_confirmation: dict[int, list[Pivot]] = {}
    for pivot in highs:
        high_by_confirmation.setdefault(pivot.confirmation_index, []).append(pivot)
    for pivot in lows:
        low_by_confirmation.setdefault(pivot.confirmation_index, []).append(pivot)

    latest_high: Pivot | None = None
    latest_low: Pivot | None = None
    broken_highs: set[int] = set()
    broken_lows: set[int] = set()
    latest: StructureBreak | None = None

    for index, candle in enumerate(bars):
        if not candle.complete:
            continue
        bullish = (
            latest_high is not None
            and latest_high.time not in broken_highs
            and index > latest_high.confirmation_index
            and candle.close > latest_high.price
        )
        bearish = (
            latest_low is not None
            and latest_low.time not in broken_lows
            and index > latest_low.confirmation_index
            and candle.close < latest_low.price
        )
        if bullish and bearish:
            # A single close through both opposing levels is structurally ambiguous.
            latest = None
        elif bullish:
            broken_highs.add(latest_high.time)
            latest = StructureBreak(Direction.BUY, latest_high.price, latest_high.time, candle.time)
        elif bearish:
            broken_lows.add(latest_low.time)
            latest = StructureBreak(Direction.SELL, latest_low.price, latest_low.time, candle.time)

        # A pivot confirmed on this candle is usable only by later closes.
        if index in high_by_confirmation:
            latest_high = high_by_confirmation[index][-1]
        if index in low_by_confirmation:
            latest_low = low_by_confirmation[index][-1]
    return latest


def detect_sweep_reclaim(
    bars: Sequence[Bar],
    direction: Direction,
    level: float,
    threshold: float,
    level_confirmed_index: int,
    symbol: str,
    *,
    max_level_age: int = 100,
    max_reclaim_bars: int = 3,
) -> LiquiditySweep | None:
    """Find a completed excursion and reclaim within a bounded event window."""
    if not math.isfinite(level) or not math.isfinite(threshold) or threshold <= 0:
        raise ValueError("level must be finite and threshold must be positive")
    if level_confirmed_index < 0 or max_level_age < 0 or max_reclaim_bars < 0:
        raise ValueError("indices and windows must be non-negative")
    closed = _closed_indices(bars)
    if not closed or level_confirmed_index >= len(bars):
        return None
    last_index = closed[-1]
    if last_index - level_confirmed_index > max_level_age:
        return None

    active: _ActiveSweep | None = None
    for index in closed:
        if index <= level_confirmed_index:
            continue
        candle = bars[index]
        if active is not None and index - active.index > max_reclaim_bars:
            active = None

        crossed = (
            candle.low <= level - threshold
            if direction is Direction.BUY
            else candle.high >= level + threshold
        )
        if crossed:
            if active is None:
                active = _ActiveSweep(index, candle.time, candle.low if direction is Direction.BUY else candle.high)
            else:
                extreme = min(active.extreme, candle.low) if direction is Direction.BUY else max(active.extreme, candle.high)
                active = _ActiveSweep(active.index, active.time, extreme)

        if active is None:
            continue
        reclaimed = candle.close > level if direction is Direction.BUY else candle.close < level
        if reclaimed and index - active.index <= max_reclaim_bars:
            return LiquiditySweep(
                direction,
                level,
                active.extreme,
                active.time,
                candle.time,
                f"{symbol}|{direction.value}|{level:.10f}|{active.time}",
            )
    return None


def qualifies_displacement(bar: Bar, atr: float, direction: Direction) -> bool:
    """Frozen body ≥1 ATR, body/range ≥0.60, close in the outer range quartile."""
    if not bar.complete or not math.isfinite(atr) or atr <= 0:
        return False
    candle_range = bar.high - bar.low
    body = abs(bar.close - bar.open)
    if candle_range <= 0 or body < atr or body / candle_range < 0.60:
        return False
    close_location = (bar.close - bar.low) / candle_range
    if direction is Direction.BUY:
        return bar.close > bar.open and close_location >= 0.75
    return bar.close < bar.open and close_location <= 0.25


def detect_fvg(
    candles: Sequence[Bar],
    direction: Direction,
    tick_size: float,
    sweep_time: int,
    displacement_time: int,
) -> FairValueGap | None:
    """Validate chronological C1/C2/C3 where C2 is the qualifying displacement."""
    if len(candles) != 3 or tick_size <= 0 or not math.isfinite(tick_size):
        return None
    c1, c2, c3 = candles
    if not all(candle.complete for candle in candles):
        return None
    if not (sweep_time < c2.time == displacement_time < c3.time):
        return None
    if direction is Direction.BUY:
        lower, upper = c1.high, c3.low
        if upper <= lower or upper - lower < tick_size:
            return None
    else:
        lower, upper = c3.high, c1.low
        if upper <= lower or upper - lower < tick_size:
            return None
    return FairValueGap(direction, lower, upper, c1.time, c2.time, c3.time)


def fvg_status(gap: FairValueGap, later_bars: Iterable[Bar]) -> FVGStatus:
    """Classify post-creation mitigation; invalidation takes precedence."""
    status = FVGStatus.FRESH
    for candle in later_bars:
        if not candle.complete:
            continue
        if gap.direction is Direction.BUY:
            if candle.close < gap.lower:
                return FVGStatus.INVALIDATED
            if candle.low <= gap.lower:
                status = FVGStatus.CONSUMED
            elif candle.low <= gap.upper and status is FVGStatus.FRESH:
                status = FVGStatus.PARTIALLY_MITIGATED
        else:
            if candle.close > gap.upper:
                return FVGStatus.INVALIDATED
            if candle.high >= gap.upper:
                status = FVGStatus.CONSUMED
            elif candle.high >= gap.lower and status is FVGStatus.FRESH:
                status = FVGStatus.PARTIALLY_MITIGATED
    return status


def midpoint(lower: float, upper: float) -> float:
    if not math.isfinite(lower) or not math.isfinite(upper) or upper <= lower:
        raise ValueError("FVG boundaries must be finite and ordered")
    return lower + (upper - lower) / 2.0


def pending_expiry(reference_time: int, candle_seconds: int = 300) -> int:
    if candle_seconds <= 0:
        raise ValueError("candle_seconds must be positive")
    return min(reference_time + 6 * candle_seconds, reference_time + 30 * 60)


def normalize_volume(
    risk_budget: float,
    loss_per_lot: float,
    minimum: float,
    maximum: float,
    step: float,
) -> float:
    """Floor valid lots to step; return zero rather than uplifting to minimum."""
    values = (risk_budget, loss_per_lot, minimum, maximum, step)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("volume and risk properties must be finite")
    if risk_budget <= 0 or loss_per_lot <= 0 or minimum <= 0 or maximum < minimum or step <= 0:
        raise ValueError("invalid broker volume/risk properties")
    raw = Decimal(str(risk_budget)) / Decimal(str(loss_per_lot))
    min_dec, max_dec, step_dec = map(Decimal, map(str, (minimum, maximum, step)))
    if raw < min_dec:
        return 0.0
    step_count = (raw / step_dec).to_integral_value(rounding=ROUND_FLOOR)
    max_steps = (max_dec / step_dec).to_integral_value(rounding=ROUND_FLOOR)
    lots = min(step_count, max_steps) * step_dec
    if lots < min_dec:
        return 0.0
    return float(lots)


def validate_stop_distance(entry: float, stop: float, point: float, stops_level_points: int) -> bool:
    if not all(math.isfinite(value) for value in (entry, stop, point)):
        return False
    if point <= 0 or stops_level_points < 0:
        return False
    return abs(entry - stop) + point * 1e-9 >= point * stops_level_points


def spread_is_allowed(bid: float, ask: float, maximum: float) -> bool:
    if not all(math.isfinite(value) for value in (bid, ask, maximum)) or ask < bid or maximum < 0:
        return False
    return ask - bid <= maximum


def net_reward_risk(gross_reward: float, gross_risk: float, round_turn_cost: float) -> float:
    if not all(math.isfinite(value) for value in (gross_reward, gross_risk, round_turn_cost)):
        return 0.0
    if gross_reward <= round_turn_cost or gross_risk <= 0 or round_turn_cost < 0:
        return 0.0
    return (gross_reward - round_turn_cost) / (gross_risk + round_turn_cost)


def atr_sma(bars: Sequence[Bar], period: int = 14) -> float | None:
    """SMA ATR over the latest completed bars; no unfinished rate is sampled."""
    if period < 1:
        raise ValueError("period must be positive")
    if len(bars) < period or not all(candle.complete for candle in bars[-period:]):
        return None
    start = len(bars) - period
    true_ranges: list[float] = []
    for index in range(start, len(bars)):
        candle = bars[index]
        if index == 0:
            true_ranges.append(candle.high - candle.low)
        else:
            previous_close = bars[index - 1].close
            if not bars[index - 1].complete:
                return None
            true_ranges.append(
                max(
                    candle.high - candle.low,
                    abs(candle.high - previous_close),
                    abs(candle.low - previous_close),
                )
            )
    return sum(true_ranges) / period


def risk_loss_per_lot(
    entry: float,
    stop: float,
    tick_size: float,
    tick_value_loss: float,
    order_calc_profit_loss: float | None,
    tolerance: float = 0.10,
) -> float:
    """Validate per-lot monetary loss against broker tick properties."""
    values = (entry, stop, tick_size, tick_value_loss, tolerance)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("risk inputs must be finite")
    if tick_size <= 0 or tick_value_loss <= 0 or tolerance < 0:
        raise ValueError("invalid tick size/value or comparison tolerance")
    tick_loss = abs(entry - stop) / tick_size * tick_value_loss
    if not math.isfinite(tick_loss) or tick_loss <= 0:
        raise ValueError("tick-based loss is not positive")
    if order_calc_profit_loss is None:
        return tick_loss
    if not math.isfinite(order_calc_profit_loss) or order_calc_profit_loss <= 0:
        raise ValueError("OrderCalcProfit loss must be positive")
    relative_difference = abs(tick_loss - order_calc_profit_loss) / order_calc_profit_loss
    if relative_difference > tolerance:
        raise ValueError("tick loss and OrderCalcProfit disagree beyond tolerance")
    return order_calc_profit_loss


def daily_loss_reached(equity_reference: float, current_equity: float, cap_percent: float) -> bool:
    if not all(math.isfinite(value) for value in (equity_reference, current_equity, cap_percent)):
        raise ValueError("equity and cap values must be finite")
    if equity_reference <= 0 or cap_percent <= 0 or cap_percent > 100:
        raise ValueError("invalid equity reference or cap")
    return current_equity <= equity_reference * (1.0 - cap_percent / 100.0)


def consecutive_loss_count(completed_lifecycle_pnls: Iterable[float]) -> int:
    streak = 0
    for pnl in completed_lifecycle_pnls:
        if not math.isfinite(pnl):
            raise ValueError("lifecycle PnL must be finite")
        if pnl < 0:
            streak += 1
        else:
            streak = 0
    return streak


def valid_limit_entry(direction: Direction, price: float, bid: float, ask: float) -> bool:
    if not all(math.isfinite(value) for value in (price, bid, ask)) or bid <= 0 or ask < bid:
        return False
    if direction is Direction.BUY:
        return 0 < price < ask
    return price > bid


def transition_setup(state: SetupState, event: str) -> SetupState:
    transitions = {
        (SetupState.WAIT_BIAS, "bias_confirmed"): SetupState.WAIT_SWEEP,
        (SetupState.WAIT_SWEEP, "sweep_confirmed"): SetupState.WAIT_DISPLACEMENT_CHOCH,
        (SetupState.WAIT_DISPLACEMENT_CHOCH, "displacement_choch_confirmed"): SetupState.WAIT_FVG,
        (SetupState.WAIT_FVG, "fvg_confirmed"): SetupState.WAIT_RETRACE_OR_ORDER,
        (SetupState.WAIT_RETRACE_OR_ORDER, "limit_order_submitted"): SetupState.ORDER_PENDING,
        (SetupState.ORDER_PENDING, "order_filled"): SetupState.IN_POSITION,
        (SetupState.ORDER_PENDING, "order_expired"): SetupState.EXPIRED,
        (SetupState.ORDER_PENDING, "setup_invalidated"): SetupState.INVALIDATED,
        (SetupState.IN_POSITION, "position_closed"): SetupState.COMPLETED,
        (SetupState.WAIT_SWEEP, "setup_invalidated"): SetupState.INVALIDATED,
        (SetupState.WAIT_DISPLACEMENT_CHOCH, "setup_invalidated"): SetupState.INVALIDATED,
        (SetupState.WAIT_FVG, "setup_invalidated"): SetupState.INVALIDATED,
        (SetupState.WAIT_RETRACE_OR_ORDER, "setup_invalidated"): SetupState.INVALIDATED,
        (SetupState.WAIT_RETRACE_OR_ORDER, "setup_expired"): SetupState.EXPIRED,
    }
    try:
        return transitions[(state, event)]
    except KeyError as error:
        raise InvalidTransition(f"event {event!r} is not valid from {state.value}") from error


def lifecycle_net_pnl(deals: Iterable[TradeDeal], position_id: int, magic: int) -> float:
    """Sum actual deal PnL and costs for one EA-owned position lifecycle."""
    return sum(
        deal.profit + deal.commission + deal.swap + deal.fee
        for deal in deals
        if deal.position_id == position_id and deal.magic == magic
    )


def detect_bos_sequence(bars: Sequence[Bar], span: int = 2) -> list[StructureBreak]:
    """Return every confirmed close-only break in chronological order.

    Used for long backtests: `detect_bos` returns only the most recent break,
    which would silently restrict a multi-year run to a single signal window.
    """
    highs, lows = confirmed_pivots(bars, span)
    high_by_confirmation: dict[int, list[Pivot]] = {}
    low_by_confirmation: dict[int, list[Pivot]] = {}
    for pivot in highs:
        high_by_confirmation.setdefault(pivot.confirmation_index, []).append(pivot)
    for pivot in lows:
        low_by_confirmation.setdefault(pivot.confirmation_index, []).append(pivot)

    latest_high: Pivot | None = None
    latest_low: Pivot | None = None
    broken_highs: set[int] = set()
    broken_lows: set[int] = set()
    events: list[StructureBreak] = []

    for index, candle in enumerate(bars):
        if not candle.complete:
            continue
        bullish = (
            latest_high is not None
            and latest_high.time not in broken_highs
            and index > latest_high.confirmation_index
            and candle.close > latest_high.price
        )
        bearish = (
            latest_low is not None
            and latest_low.time not in broken_lows
            and index > latest_low.confirmation_index
            and candle.close < latest_low.price
        )
        if bullish and bearish:
            pass  # Structurally ambiguous: no event for this candle.
        elif bullish:
            broken_highs.add(latest_high.time)
            events.append(StructureBreak(Direction.BUY, latest_high.price, latest_high.time, candle.time))
        elif bearish:
            broken_lows.add(latest_low.time)
            events.append(StructureBreak(Direction.SELL, latest_low.price, latest_low.time, candle.time))

        if index in high_by_confirmation:
            latest_high = high_by_confirmation[index][-1]
        if index in low_by_confirmation:
            latest_low = low_by_confirmation[index][-1]
    return events


def select_live_sweep(
    candidates: Sequence[LiquiditySweep], now: int, window_seconds: int
) -> LiquiditySweep | None:
    """Choose the sweep that can still develop into a setup, or nothing.

    A sweep is live only while its remaining development window has not elapsed.
    Stale sweeps are rejected rather than returned so the caller can wait for a new
    event instead of immediately building a setup that would expire. Among live
    candidates the most recent confirmation wins, consistent with how the latest
    structural break sets bias.
    """
    if window_seconds <= 0:
        raise ValueError("window_seconds must be positive")
    live = [
        candidate
        for candidate in candidates
        if candidate.confirmation_time <= now <= candidate.confirmation_time + window_seconds
    ]
    if not live:
        return None
    return max(live, key=lambda candidate: candidate.confirmation_time)


def _is_pivot_high(bars: Sequence[Bar], index: int, span: int) -> bool:
    window = bars[index - span : index + span + 1]
    if len(window) != span * 2 + 1 or not all(b.complete for b in window):
        return False
    centre = bars[index]
    return all(centre.high > b.high for b in window if b is not centre)


def _is_pivot_low(bars: Sequence[Bar], index: int, span: int) -> bool:
    window = bars[index - span : index + span + 1]
    if len(window) != span * 2 + 1 or not all(b.complete for b in window):
        return False
    centre = bars[index]
    return all(centre.low < b.low for b in window if b is not centre)


def next_unswept_liquidity(
    bars: Sequence[Bar], entry: float, direction: Direction, span: int, tick_size: float
) -> float | None:
    """Nearest confirmed opposing pivot above/below entry that is still intact.

    A level that price has already traded through after the pivot was confirmed is
    no longer resting liquidity, so it cannot be used as a target.
    """
    if tick_size <= 0 or entry <= 0 or direction not in (Direction.BUY, Direction.SELL):
        raise ValueError("invalid entry, tick size, or direction")
    if len(bars) < span * 2 + 1:
        return None
    closed_end = max(index for index, b in enumerate(bars) if b.complete)
    best: float | None = None
    for index in range(span, closed_end - span + 1):
        if bars[index].time > bars[closed_end].time:
            continue
        if direction is Direction.BUY and _is_pivot_high(bars, index, span):
            level = bars[index].high
            if level <= entry + tick_size:
                continue
            intact = not any(
                bars[k].complete and bars[k].high >= level for k in range(index + 1, closed_end + 1)
            )
            if intact and (best is None or level < best):
                best = level
        elif direction is Direction.SELL and _is_pivot_low(bars, index, span):
            level = bars[index].low
            if level >= entry - tick_size:
                continue
            intact = not any(
                bars[k].complete and bars[k].low <= level for k in range(index + 1, closed_end + 1)
            )
            if intact and (best is None or level > best):
                best = level
    return best
