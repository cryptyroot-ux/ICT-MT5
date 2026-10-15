"""B0-B3 experiment runner for SMC-H1M5-SweepFVG.

This is a research harness, not the trading EA and not a MetaEditor compile test.
It refuses to produce OOS claims from synthetic, third-party, or unverified data,
and reports INCONCLUSIVE rather than PASS when the sample is too small.

B0 note: the legacy ICT-MT5 EA does not compile as received (docs/SOURCE_AUDIT.md
AUD-001) and is preserved unmodified here, so B0 cannot be re-implemented in
Python and still be called "the original baseline". B0 therefore requires a
documented repair plus a MetaEditor/Strategy Tester run and is reported as
BLOCKED until that evidence exists.
"""

from __future__ import annotations

import csv
import hashlib
import math
import random
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable, Literal, Sequence

from research.smc_core import (
    Bar,
    Direction,
    FVGStatus,
    confirmed_pivots,
    detect_bos,
    detect_fvg,
    detect_sweep_reclaim,
    fvg_status,
    midpoint,
    net_reward_risk,
    normalize_volume,
    pending_expiry,
    qualifies_displacement,
    atr_sma,
)

Variants = ("B0", "B1", "B2", "B3")
SourceKind = Literal["broker_export", "third_party", "synthetic"]
DataType = Literal["real_ticks", "bar_model"]


class EvidenceError(RuntimeError):
    """Raised when the supplied evidence cannot support the requested claim."""


@dataclass(frozen=True)
class DataManifest:
    symbol: str
    broker: str
    account_server: str
    timezone: str
    start: str
    end: str
    data_type: DataType
    source_kind: SourceKind
    sha256: str
    spread_model: str
    commission_per_lot: float
    swap_model: str
    slippage_per_lot: float
    account_currency: str
    initial_equity: float
    tick_size: float
    tick_value_loss: float
    tick_value_profit: float
    volume_min: float
    volume_max: float
    volume_step: float
    stops_level_points: float
    freeze_level_points: float
    point: float
    digits: int
    contract_size: float
    notes: str = ""


@dataclass(frozen=True)
class FrozenConfig:
    risk_percent: float = 0.50
    daily_loss_percent: float = 2.0
    max_consecutive_losses: int = 2
    max_spread_price: float = 0.50
    min_net_rr: float = 3.0
    atr_period: int = 14
    atr_stop_buffer: float = 0.5
    swing_span: int = 2
    max_liquidity_age_bars: int = 100
    sweep_threshold_ticks: float = 2.0
    max_reclaim_bars: int = 3
    pending_expiry_bars: int = 6
    pending_expiry_seconds: int = 1800
    displacement_atr_multiple: float = 1.0
    displacement_body_ratio: float = 0.60
    displacement_close_quartile: float = 0.25
    entry_model: str = "fvg_midpoint"


@dataclass
class Trade:
    setup_id: str
    variant: str
    direction: str
    entry_time: int
    entry: float
    stop: float
    target: float
    volume: float
    risk_amount: float
    exit_time: int
    exit_reason: str
    pnl: float
    gross_reward: float
    round_turn_cost: float
    bars_in_market: int

    @property
    def r_multiple(self) -> float:
        return self.pnl / self.risk_amount if self.risk_amount else 0.0


# --------------------------------------------------------------------------- #
# Evidence gate
# --------------------------------------------------------------------------- #
def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_manifest(manifest: DataManifest, data_path: Path, require_oos: bool = True) -> None:
    """Refuse any dataset that cannot support a broker OOS claim."""
    if manifest.symbol != "XAUUSD":
        raise EvidenceError(f"target instrument must be XAUUSD, got {manifest.symbol!r}")
    if manifest.initial_equity <= 0 or not math.isfinite(manifest.initial_equity):
        raise EvidenceError("initial_equity must be positive and finite")
    for name, value in (
        ("tick_size", manifest.tick_size),
        ("tick_value_loss", manifest.tick_value_loss),
        ("tick_value_profit", manifest.tick_value_profit),
        ("volume_min", manifest.volume_min),
        ("volume_step", manifest.volume_step),
        ("point", manifest.point),
        ("contract_size", manifest.contract_size),
    ):
        if value <= 0 or not math.isfinite(value):
            raise EvidenceError(f"invalid broker property {name}={value!r}")
    if manifest.volume_max < manifest.volume_min:
        raise EvidenceError("volume_max must be >= volume_min")
    if manifest.commission_per_lot < 0 or manifest.slippage_per_lot < 0:
        raise EvidenceError("transaction costs cannot be negative")

    if not data_path.is_file():
        raise EvidenceError(f"data file not found: {data_path}")
    actual = file_sha256(data_path)
    if actual != manifest.sha256:
        raise EvidenceError(f"data sha256 mismatch: manifest={manifest.sha256} actual={actual}")

    if require_oos:
        if manifest.source_kind == "synthetic":
            raise EvidenceError("synthetic data cannot support an out-of-sample claim")
        if manifest.source_kind == "third_party":
            raise EvidenceError("third-party data cannot support a broker out-of-sample claim")
        if manifest.data_type == "bar_model":
            raise EvidenceError(
                "bar-model data may support exploratory work only, not a broker-grade OOS claim"
            )


# --------------------------------------------------------------------------- #
# Data loading
# --------------------------------------------------------------------------- #
def load_bars(path: Path) -> list[Bar]:
    bars: list[Bar] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            bars.append(
                Bar(
                    time=int(row["time"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    complete=row.get("complete", "1").strip() not in ("0", "false", "False"),
                )
            )
    if not bars:
        raise EvidenceError("no bars parsed")
    for earlier, later in zip(bars, bars[1:]):
        if later.time <= earlier.time:
            raise EvidenceError("bar timestamps must be strictly increasing (no duplicates/gaps in order)")
    return bars


def aggregate_h1(m5_bars: Sequence[Bar]) -> list[Bar]:
    """Aggregate closed M5 bars into closed H1 bars using explicit chronological order."""
    if not m5_bars:
        return []
    hours: dict[int, list[Bar]] = {}
    for bar in m5_bars:
        if not bar.complete:
            continue
        hours.setdefault(bar.time // 3600 * 3600, []).append(bar)
    result: list[Bar] = []
    for hour_start in sorted(hours):
        bucket = sorted(hours[hour_start], key=lambda b: b.time)
        if len(bucket) < 12:
            continue  # Ignore incomplete hours (session gaps are not counted as closed H1 bars).
        result.append(
            Bar(
                time=hour_start,
                open=bucket[0].open,
                high=max(b.high for b in bucket),
                low=min(b.low for b in bucket),
                close=bucket[-1].close,
                complete=True,
            )
        )
    return result


# --------------------------------------------------------------------------- #
# Segmentation with boundary purging
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Segment:
    name: str
    start: int
    end: int


def chronological_segments(
    bars: Sequence[Bar], train_ratio: float = 0.6, validation_ratio: float = 0.2
) -> list[Segment]:
    if not 0 < train_ratio < 1 or not 0 < validation_ratio < 1:
        raise EvidenceError("split ratios must be within (0,1)")
    if train_ratio + validation_ratio >= 1:
        raise EvidenceError("train+validation must leave an out-of-sample remainder")
    times = [bar.time for bar in bars]
    total = len(times)
    train_end = times[int(total * train_ratio) - 1]
    validation_end = times[int(total * (train_ratio + validation_ratio)) - 1]
    return [
        Segment("in_sample", times[0], train_end),
        Segment("validation", train_end + 1, validation_end),
        Segment("out_of_sample", validation_end + 1, times[-1]),
    ]


def purge_boundary_crossers(trades: Iterable[Trade], segments: Sequence[Segment]) -> dict[str, list[Trade]]:
    """Drop trades whose lifecycle touches more than one segment; no state leaks across splits."""
    ordered = sorted(segments, key=lambda s: s.start)
    buckets: dict[str, list[Trade]] = {segment.name: [] for segment in ordered}
    for trade in trades:
        owners = [
            segment.name
            for segment in ordered
            if trade.entry_time <= segment.end and trade.exit_time >= segment.start
        ]
        if len(owners) == 1:
            buckets[owners[0]].append(trade)
    return buckets


# --------------------------------------------------------------------------- #
# Signal construction
# --------------------------------------------------------------------------- #
def _sweep_confirms(
    m5: Sequence[Bar], index: int, direction: Direction, config: FrozenConfig, tick_size: float
) -> tuple[bool, float]:
    """Look backwards from a closed bar for the frozen excursion+reclaim sequence."""
    level = None
    confirmed_index = None
    for pivot_index in range(index - 1, max(-1, index - config.max_liquidity_age_bars - config.swing_span), -1):
        window = m5[max(0, pivot_index - config.swing_span) : pivot_index + config.swing_span + 1]
        if len(window) < config.swing_span * 2 + 1 or not all(b.complete for b in window):
            continue
        centre = m5[pivot_index]
        is_low = all(centre.low < b.low for b in window if b is not centre)
        is_high = all(centre.high > b.high for b in window if b is not centre)
        if direction is Direction.BUY and is_low:
            level = centre.low
        elif direction is Direction.SELL and is_high:
            level = centre.high
        if level is not None:
            confirmed_index = pivot_index + config.swing_span
            break
    if level is None or confirmed_index is None or confirmed_index > index:
        return False, 0.0

    threshold = config.sweep_threshold_ticks * tick_size
    if direction is Direction.BUY and m5[index].low > level - threshold:
        return False, 0.0
    if direction is Direction.SELL and m5[index].high < level + threshold:
        return False, 0.0

    extreme = m5[index].low if direction is Direction.BUY else m5[index].high
    for probe in range(index, min(len(m5), index + config.max_reclaim_bars + 1)):
        bar = m5[probe]
        if not bar.complete:
            break
        extreme = min(extreme, bar.low) if direction is Direction.BUY else max(extreme, bar.high)
        reclaimed = bar.close > level if direction is Direction.BUY else bar.close < level
        if reclaimed:
            return True, extreme
    return False, 0.0


def run_variant(
    m5_bars: Sequence[Bar],
    h1_bars: Sequence[Bar],
    variant: str,
    manifest: DataManifest,
    config: FrozenConfig,
    start: int,
    end: int,
    bars_in_market_max: int = 240,
) -> list[Trade]:
    """Evaluate one frozen variant over [start, end] using only closed bars inside the window."""
    if variant not in Variants:
        raise EvidenceError(f"unknown variant {variant!r}; expected one of {Variants}")
    if variant == "B0":
        raise EvidenceError(
            "B0 requires a documented repair of the legacy EA plus a MetaEditor/Strategy Tester "
            "run; it cannot be produced by this Python harness"
        )

    m5 = [bar for bar in m5_bars if start <= bar.time <= end and bar.complete]
    h1 = [bar for bar in h1_bars if start <= bar.time <= end and bar.complete]
    if len(m5) < 40 or len(h1) < 10:
        return []

    break_event = detect_bos(h1, span=config.swing_span)
    if break_event is None:
        return []

    trades: list[Trade] = []
    consumed_bias_time = 0
    for index in range(6, len(m5) - config.atr_period - 2):
        if break_event.confirmation_time > m5[index].time:
            continue
        if m5[index].time <= consumed_bias_time:
            continue

        direction = break_event.direction
        confirmed, extreme = _sweep_confirms(m5, index, direction, config, manifest.tick_size)
        if not confirmed:
            continue
        sweep_time = m5[index].time
        consumed_bias_time = break_event.confirmation_time

        atr = atr_sma(m5[max(0, index - config.atr_period - 1) : index + 1], period=config.atr_period)
        if atr is None or atr <= 0:
            continue

        entry = stop = target = 0.0
        reference_time = sweep_time
        setup_id = f"{variant}|{direction.value}|{sweep_time}|{extreme:.2f}"

        if variant in ("B2", "B3"):
            displacement_ok = False
            for probe in range(index + 1, min(len(m5), index + 13)):
                atr_probe = atr_sma(m5[max(0, probe - config.atr_period - 1) : probe + 1], period=config.atr_period)
                if atr_probe is None or atr_probe <= 0:
                    continue
                if qualifies_displacement(m5[probe], config.displacement_atr_multiple * atr_probe, direction):
                    displacement_ok = True
                    break
            if not displacement_ok:
                continue

        if variant == "B3":
            gap_start = None
            for probe in range(index + 1, min(len(m5), index + 13)):
                atr_probe = atr_sma(m5[max(0, probe - config.atr_period - 1) : probe + 1], period=config.atr_period)
                if atr_probe is None or atr_probe <= 0:
                    continue
                if qualifies_displacement(m5[probe], config.displacement_atr_multiple * atr_probe, direction):
                    gap_start = probe
                    break
            if gap_start is None or gap_start + 1 >= len(m5):
                continue
            candles = [m5[gap_start - 1], m5[gap_start], m5[gap_start + 1]]
            gap = detect_fvg(candles, direction, manifest.tick_size, sweep_time, m5[gap_start].time)
            if gap is None:
                continue
            entry = midpoint(gap.lower, gap.upper)
            reference_time = m5[gap_start + 1].time
            later = [bar for bar in m5[gap_start + 2 :] if bar.time > gap.created_time]
            if fvg_status(gap, later) in (FVGStatus.CONSUMED, FVGStatus.INVALIDATED):
                continue
        else:
            entry = m5[index].close

        stop = extreme - config.atr_stop_buffer * atr if direction is Direction.BUY else extreme + config.atr_stop_buffer * atr
        if direction is Direction.BUY and stop >= entry:
            continue
        if direction is Direction.SELL and stop <= entry:
            continue

        distance = abs(entry - stop)
        loss_per_lot = (distance / manifest.tick_size) * manifest.tick_value_loss
        volume = normalize_volume(
            manifest.initial_equity * config.risk_percent / 100.0,
            loss_per_lot,
            manifest.volume_min,
            manifest.volume_max,
            manifest.volume_step,
        )
        if volume <= 0:
            continue
        round_turn_cost = (
            manifest.commission_per_lot + manifest.slippage_per_lot
        ) * volume
        if net_reward_risk(distance * config.min_net_rr, distance, round_turn_cost) < config.min_net_rr:
            continue
        if pending_expiry(reference_time) <= reference_time:
            continue
        risk_amount = loss_per_lot * volume + round_turn_cost

        trades.append(
            Trade(
                setup_id=setup_id,
                variant=variant,
                direction=direction.value,
                entry_time=reference_time,
                entry=entry,
                stop=stop,
                target=entry + (config.min_net_rr * distance if direction is Direction.BUY else -config.min_net_rr * distance),
                volume=volume,
                risk_amount=risk_amount,
                exit_time=reference_time,
                exit_reason="open",
                pnl=0.0,
                gross_reward=config.min_net_rr * distance * volume,
                round_turn_cost=round_turn_cost,
                bars_in_market=0,
            )
        )
    return _resolve_exits(m5, trades, bars_in_market_max)


def _resolve_exits(m5: Sequence[Bar], trades: list[Trade], bars_in_market_max: int) -> list[Trade]:
    index_by_time = {bar.time: index for index, bar in enumerate(m5)}
    resolved: list[Trade] = []
    for trade in trades:
        start_index = index_by_time.get(trade.entry_time)
        if start_index is None:
            continue
        exit_index = min(start_index + bars_in_market_max, len(m5) - 1)
        reason = "timeout"
        exit_time = m5[exit_index].time
        price_delta = 0.0
        for probe in range(start_index + 1, exit_index + 1):
            bar = m5[probe]
            if trade.direction == "BUY":
                if bar.low <= trade.stop:
                    reason, price_delta, exit_index, exit_time = "stop", trade.stop - trade.entry, probe, bar.time
                    break
                if bar.high >= trade.target:
                    reason, price_delta, exit_index, exit_time = "target", trade.target - trade.entry, probe, bar.time
                    break
            else:
                if bar.high >= trade.stop:
                    reason, price_delta, exit_index, exit_time = "stop", trade.entry - trade.stop, probe, bar.time
                    break
                if bar.low <= trade.target:
                    reason, price_delta, exit_index, exit_time = "target", trade.entry - trade.target, probe, bar.time
                    break
        trade.exit_reason = reason
        trade.exit_time = exit_time
        trade.bars_in_market = exit_index - start_index
        trade.pnl = price_delta * trade.volume - trade.round_turn_cost
        resolved.append(trade)
    return resolved


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #
@dataclass
class Metrics:
    trades: int
    win_rate: float
    avg_win_r: float
    avg_loss_r: float
    expectancy_r: float
    profit_factor: float
    gross_pnl: float
    net_pnl: float
    max_drawdown_pct: float
    max_consecutive_losses: int
    total_cost: float
    expectancy_ci_low: float | None
    expectancy_ci_high: float | None
    bootstrap_seed: int
    bootstrap_resamples: int
    bootstrap_block: int


def block_bootstrap_expectancy(
    r_multiples: Sequence[float], seed: int, resamples: int = 2000, block: int = 4
) -> tuple[float | None, float | None]:
    """Block bootstrap CI that preserves serial dependence between trades."""
    if len(r_multiples) < max(10, block * 3):
        return None, None
    rng = random.Random(seed)
    n = len(r_multiples)
    means = []
    for _ in range(resamples):
        sample: list[float] = []
        while len(sample) < n:
            start = rng.randrange(0, max(1, n - block + 1))
            sample.extend(r_multiples[start : start + block])
        means.append(sum(sample[:n]) / n)
    means.sort()
    low = means[int(0.025 * (resamples - 1))]
    high = means[int(0.975 * (resamples - 1))]
    return low, high


def compute_metrics(
    trades: Sequence[Trade], equity_reference: float, seed: int = 20261015
) -> Metrics:
    if not trades:
        return Metrics(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0.0, None, None, seed, 0, 0)
    ordered = sorted(trades, key=lambda t: t.exit_time)
    wins = [t.r_multiple for t in ordered if t.pnl > 0]
    losses = [t.r_multiple for t in ordered if t.pnl <= 0]
    gross_profit = sum(t.pnl for t in ordered if t.pnl > 0)
    gross_loss = abs(sum(t.pnl for t in ordered if t.pnl <= 0))

    equity = equity_reference
    peak = equity_reference
    max_dd = 0.0
    streak = 0
    max_streak = 0
    for trade in ordered:
        equity += trade.pnl
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak * 100.0)
        streak = streak + 1 if trade.pnl <= 0 else 0
        max_streak = max(max_streak, streak)

    rs = [t.r_multiple for t in ordered]
    low, high = block_bootstrap_expectancy(rs, seed=seed)
    return Metrics(
        trades=len(ordered),
        win_rate=100.0 * len(wins) / len(ordered),
        avg_win_r=(sum(wins) / len(wins)) if wins else 0.0,
        avg_loss_r=(sum(losses) / len(losses)) if losses else 0.0,
        expectancy_r=sum(rs) / len(rs),
        profit_factor=(gross_profit / gross_loss) if gross_loss > 0 else float("inf"),
        gross_pnl=gross_profit - gross_loss,
        net_pnl=sum(t.pnl for t in ordered),
        max_drawdown_pct=max_dd,
        max_consecutive_losses=max_streak,
        total_cost=sum(t.round_turn_cost for t in ordered),
        expectancy_ci_low=low,
        expectancy_ci_high=high,
        bootstrap_seed=seed,
        bootstrap_resamples=2000,
        bootstrap_block=4,
    )


# --------------------------------------------------------------------------- #
# Frozen acceptance gates
# --------------------------------------------------------------------------- #
@dataclass
class Gate:
    name: str
    threshold: str
    measured: str
    status: Literal["PASS", "FAIL", "INCONCLUSIVE"]


def evaluate_gates(metrics: Metrics, min_trades: int = 100) -> list[Gate]:
    gates = [
        Gate("net_expectancy_r", ">= +0.25R", f"{metrics.expectancy_r:+.4f}R", "FAIL"),
        Gate("profit_factor", ">= 1.50", f"{metrics.profit_factor:.3f}", "FAIL"),
        Gate("max_drawdown_pct", "< 12%", f"{metrics.max_drawdown_pct:.2f}%", "FAIL"),
    ]
    for gate in gates:
        if metrics.trades < min_trades:
            gate.status = "INCONCLUSIVE"
    sample_gate = Gate(
        "completed_trades",
        f">= {min_trades}",
        str(metrics.trades),
        "PASS" if metrics.trades >= min_trades else "INCONCLUSIVE",
    )
    gates.append(sample_gate)
    if metrics.trades >= min_trades:
        if metrics.expectancy_r >= 0.25:
            gates[0].status = "PASS"
        if metrics.profit_factor >= 1.50:
            gates[1].status = "PASS"
        if metrics.max_drawdown_pct < 12.0:
            gates[2].status = "PASS"
    return gates


def write_trade_csv(trades: Sequence[Trade], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(asdict(trades[0]).keys()) + ["r_multiple"] if trades else [
        "setup_id", "variant", "direction", "entry_time", "entry", "stop", "target",
        "volume", "risk_amount", "exit_time", "exit_reason", "pnl", "gross_reward",
        "round_turn_cost", "bars_in_market", "r_multiple",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for trade in trades:
            row = asdict(trade)
            row["r_multiple"] = trade.r_multiple
            writer.writerow(row)
    return path
