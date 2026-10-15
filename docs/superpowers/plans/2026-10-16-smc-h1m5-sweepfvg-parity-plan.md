# SMC-H1M5-SweepFVG V2 Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Make the experimental V2 EA and its Python reference model causal, execution-equivalent, observable, and fail-closed when transaction costs are not verified.

**Architecture:** Preserve frozen B3 and its specification unchanged. Implement a separate V2 reference runner in `research/v2_parity.py`, then align MQL V2 with the same prior-D1/session/order/stop semantics. Give V2 a distinct version, magic number, event-log identity, and observe-only mode. Run only the historical DEV interval for diagnostics; the old OOS is burned.

**Tech Stack:** MQL5 / MetaEditor build 6257, Python 3.12, standard-library `unittest`, existing `research.smc_core` and `research.experiment` primitives. No new external dependencies.

**Execution:** Inline in the dedicated `/tmp/SMC-H1M5-SweepFVG` clone on `feature/smc-h1m5-sweepfvg`, as requested. Baseline: `python3 -m unittest discover -s tests` → 80 tests, OK.

## Global Constraints

- Preserve `docs/SMC_H1M5_SWEEPFVG_SPEC.md` and B3 as the frozen historical baseline.
- The running variant is **V2 experimental**, not B3. Use V2-specific version/variant, magic number, config hash, and event log.
- Historical XAUUSD input is HFM Demo4 M5/H1 bar data from 2018-07-01. It is **not tick-accurate**.
- The 2024-04..2026-10 interval is **burned** and must not be used for a fresh OOS claim or parameter selection.
- V2 parameters already present are fixed for this parity repair: `min_sl_price=$20/oz`, `atr_multiplier=3.0`, `session_ratio=1.30`, `target_r=3.0`, `pending_expiry_bars=6`, `pending_expiry_seconds=1800`.
- Preserve 0.25% demo risk, 2% daily loss cap, two-consecutive-loss guard, and real-account hard block.
- Commission and execution slippage are not verified for the active account. `COST_VERIFIED=0` requires `OBSERVE_ONLY=1`; observe-only may log setups but must never call order submission.
- All daily/session features use only data available at the decision timestamp. All signal confirmation uses completed bars.
- B3 acceptance gates remain unchanged: at least 100 trades, expectancy ≥ +0.25R, PF ≥ 1.50, max DD < 12%, plus cost stress. Do not tune or claim profitability on burned OOS.

---

### Task 1: Causal V2 primitives (previous D1, closed-bar session)

**Files:**
- Create: `research/v2_parity.py`
- Create: `tests/test_v2_parity.py`

The test module imports `calendar`, `datetime`, `unittest`, `Direction` from `research.smc_core`, and all V2 data/filter types from `research.v2_parity`. It defines:

```python
def make_bar(stamp, high, low, close):
    return ServerBar(
        time=calendar.timegm(stamp.timetuple()), server_day=stamp.date(),
        server_hour=stamp.hour, open=close, high=high, low=low, close=close,
        spread_price=0.30, complete=True,
    )
```

**Interfaces:**
- `ServerBar(time: int, server_day: datetime.date, server_hour: int, open: float, high: float, low: float, close: float, spread_price: float, complete: bool=True)`
- `DailyRange(day: datetime.date, high: float, low: float)`
- `V2Config(swing_span=2, max_liquidity_age_bars=100, sweep_threshold_ticks=2.0, max_reclaim_bars=3, setup_development_bars=12, atr_period=14, displacement_atr=1.0, displacement_body_ratio=0.60, displacement_close_quartile=0.25, min_sl_price=20.0, atr_sl_multiplier=3.0, target_r=3.0, session_lookback_bars=200, session_ratio=1.30, pending_expiry_bars=6, pending_expiry_seconds=1800, max_spread_price=0.50, risk_percent=0.25, daily_loss_percent=2.0, observe_only=True)`; defaults are fixed, not a search grid.
- `may_submit_order(cost_verified: bool, observe_only: bool) -> bool` returns true only for `cost_verified and not observe_only`.
- `load_v2_bars(path: Path, start_time: datetime | None=None, end_time: datetime | None=None) -> list[ServerBar]`; stop reading immediately after `end_time` so burned OOS rows are not loaded.
- `aggregate_daily_ranges(bars: Sequence[ServerBar]) -> list[DailyRange]`
- `prior_daily_range(daily: Sequence[DailyRange], decision_day: date) -> DailyRange | None`
- `premium_discount_allows(direction: Direction, decision_price: float, prior_range: DailyRange | None) -> bool`
- `rolling_session_allowed(bars: Sequence[ServerBar], decision_index: int, lookback_bars: int=200, ratio: float=1.30) -> bool`; only bars strictly before `decision_index` are in the rolling window.
- Test fixture helper used below: `make_bar(stamp, high, low, close) -> ServerBar` constructs a complete bar with server calendar fields and `spread_price=0.30`.

- [ ] **Step 1: Write failing tests for the D1 lookahead leak**

```python
class PremiumDiscountCausalityTests(unittest.TestCase):
    def test_pd_uses_previous_completed_day_not_current_day_extremes(self):
        jan1 = datetime.datetime(2020, 1, 1, 23, 55)
        jan2a = datetime.datetime(2020, 1, 2, 10, 0)
        jan2b = datetime.datetime(2020, 1, 2, 15, 0)
        before = [make_bar(jan1, 120, 80, 100), make_bar(jan2a, 100, 90, 95)]
        after = before + [make_bar(jan2b, 150, 80, 120)]
        previous_before = prior_daily_range(aggregate_daily_ranges(before), jan2a.date())
        previous_after = prior_daily_range(aggregate_daily_ranges(after), jan2a.date())
        self.assertEqual(previous_before, previous_after)
        self.assertEqual(previous_before.day, datetime.date(2020, 1, 1))
        self.assertTrue(premium_discount_allows(Direction.BUY, 95.0, previous_before))
        same_day_before = DailyRange(datetime.date(2020, 1, 2), 100.0, 90.0)
        same_day_after = DailyRange(datetime.date(2020, 1, 2), 150.0, 80.0)
        self.assertFalse(premium_discount_allows(Direction.BUY, 95.0, same_day_before))
        self.assertTrue(premium_discount_allows(Direction.BUY, 95.0, same_day_after))

    def test_current_day_range_is_not_a_valid_prior_range(self):
        first = datetime.datetime(2020, 1, 1, 12, 0)
        second = datetime.datetime(2020, 1, 2, 12, 0)
        bars = [make_bar(first, 110, 90, 100), make_bar(second, 130, 80, 100)]
        daily = aggregate_daily_ranges(bars)
        chosen = prior_daily_range(daily, datetime.date(2020, 1, 2))
        self.assertEqual(chosen.day, datetime.date(2020, 1, 1))


class SessionWindowTests(unittest.TestCase):
    def test_rolling_session_uses_only_bars_before_decision(self):
        day = datetime.date(2020, 1, 1)
        history = [ServerBar(i*300, day, 9, 100, 101, 99, 100, 0.30) for i in range(200)]
        decision = ServerBar(200*300, day, 10, 100, 200, 50, 100, 0.30)
        self.assertFalse(rolling_session_allowed(history + [decision], decision_index=200))

    def test_rolling_session_is_limited_to_the_last_200_closed_bars(self):
        day = datetime.date(2020, 1, 1)
        history = [range_bar(0, day, 9, 120.0, 20.0)]
        history += [range_bar(i + 1, day, 10, 110.0, 100.0) for i in range(100)]
        history += [range_bar(i + 101, day, 11, 102.0, 100.0) for i in range(100)]
        decision = range_bar(201, day, 10, 110.0, 100.0)
        bars = history + [decision]
        # Including the old high-range hour 9 bar would reject this decision, so a
        # True result proves the window really is the last 200 bars.
        self.assertTrue(rolling_session_allowed(bars, decision_index=201))
        self.assertFalse(rolling_session_allowed(bars, decision_index=201, lookback_bars=400))
```

The fixture above replaced an earlier draft whose two hours collapsed to the same boolean
with and without the window limit, so it could not prove the limit existed. `range_bar`
builds a bar from an explicit high/low range.

- [ ] **Step 2: Run the focused tests and confirm they fail**

Run: `python3 -m unittest tests.test_v2_parity.PremiumDiscountCausalityTests -v`  
Expected: FAIL because `research.v2_parity` and its no-lookahead functions are not implemented.

- [ ] **Step 3: Add timestamp-preserving V2 bars and causal daily/session helpers**

```python
from __future__ import annotations

import calendar
import csv
import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from research.smc_core import Direction


@dataclass(frozen=True)
class ServerBar:
    time: int
    server_day: datetime.date
    server_hour: int
    open: float
    high: float
    low: float
    close: float
    spread_price: float
    complete: bool = True


@dataclass(frozen=True)
class DailyRange:
    day: datetime.date
    high: float
    low: float


def load_v2_bars(path: Path, start_time=None, end_time=None) -> list[ServerBar]:
    bars = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            stamp = datetime.datetime.strptime(row["datetime"], "%Y.%m.%d %H:%M:%S")
            if end_time is not None and stamp > end_time:
                break  # Do not even load the burned OOS observations.
            if start_time is not None and stamp < start_time:
                continue
            bars.append(ServerBar(
                time=calendar.timegm(stamp.timetuple()), server_day=stamp.date(),
                server_hour=stamp.hour, open=float(row["open"]),
                high=float(row["high"]), low=float(row["low"]),
                close=float(row["close"]),
                spread_price=float(row["spread_points"]) * 0.01,
                complete=True,
            ))
    if not bars:
        raise ValueError(f"{path}: no M5 rows")
    for left, right in zip(bars, bars[1:]):
        if right.time <= left.time:
            raise ValueError(f"{path}: timestamps must be strictly increasing")
    return bars


def aggregate_daily_ranges(bars: Sequence[ServerBar]) -> list[DailyRange]:
    grouped: dict[datetime.date, list[ServerBar]] = {}
    for bar in bars:
        if bar.complete:
            grouped.setdefault(bar.server_day, []).append(bar)
    return [DailyRange(day, max(b.high for b in rows), min(b.low for b in rows))
            for day, rows in sorted(grouped.items())]


def prior_daily_range(daily: Sequence[DailyRange], decision_day: datetime.date) -> DailyRange | None:
    # Strictly choose a day earlier than the decision day; never the current day.
    candidates = [bar for bar in daily if bar.day < decision_day]
    return candidates[-1] if candidates else None


def premium_discount_allows(direction: Direction, decision_price: float,
                            previous_day: DailyRange | None) -> bool:
    if previous_day is None or previous_day.high <= previous_day.low:
        return False
    midpoint = (previous_day.high + previous_day.low) / 2.0
    if direction is Direction.BUY:
        return decision_price < midpoint
    return decision_price > midpoint


def rolling_session_allowed(bars: Sequence[ServerBar], decision_index: int,
                            lookback_bars: int = 200, ratio: float = 1.30) -> bool:
    # The decision bar is the first bar after C3; session history ends strictly
    # before it, matching CopyRates(..., start_pos=1, count=200).
    decision_hour = bars[decision_index].server_hour
    window = [bar for bar in bars[:decision_index] if bar.complete][-lookback_bars:]
    by_hour = {}
    for bar in window:
        by_hour.setdefault(bar.server_hour, []).append(bar.high - bar.low)
    means = {hour: sum(values) / len(values) for hour, values in by_hour.items()}
    if decision_hour not in means or not means:
        return False
    return means[decision_hour] >= (sum(means.values()) / len(means)) * ratio


def may_submit_order(cost_verified: bool, observe_only: bool) -> bool:
    # Observe-only never trades, even if a cost model is present.
    return cost_verified and not observe_only
```

The implementation also validates strictly increasing timestamps and finite OHLC/spread values. It parses server wall-clock date/hour explicitly; it does not rely on the host machine timezone.

- [ ] **Step 4: Run the focused tests**

Run: `python3 -m unittest tests.test_v2_parity.PremiumDiscountCausalityTests tests.test_v2_parity.SessionWindowTests -v`  
Expected: PASS, including the “append future bars without changing past decision” invariant.

---

### Task 2: V2 causal setup and pending-order simulator

**Files:**
- Modify: `research/v2_parity.py`
- Modify: `tests/test_v2_parity.py`

**Interfaces:**
- `V2Config` stores the existing fixed V2 constants and runtime cost model; it has no optimization grid.
- `V2Setup(c3_index: int, order_decision_index: int, direction: Direction, gap: FairValueGap, sweep: LiquiditySweep, atr: float)` stores the causal signal facts; midpoint, protection, filter results, and pending-order times are derived once.
- `PendingFill(entry_index: int, entry_price: float, decision_index: int, expiry_time: int)`
- `pending_limit_fill(bars: Sequence[ServerBar], direction: Direction, limit_price: float, submit_index: int, pending_expiry_bars=6, pending_expiry_seconds=1800) -> PendingFill | None`
- `protection_levels(direction: Direction, entry: float, sweep_extreme: float, atr: float, tick_size: float, min_sl_price=20.0, atr_multiplier=3.0, target_r=3.0) -> tuple[float, float]`
- `build_v2_candidates(m5, h1, config) -> list[V2Setup]` emits only causal FVG-confirmed order candidates.
- `simulate_v2_pending(candidates, m5, config, manifest) -> list[Trade]` models pending fills/expiry and Bid/Ask exits.
- Reuse `Direction`, `Bar`, `FairValueGap`, `LiquiditySweep`, `detect_bos_sequence`, `confirmed_pivots`, `qualifies_displacement`, and `detect_fvg` from `research.smc_core`; V2-specific event timing and pending fills live only in `research.v2_parity.py`.
- `may_submit_order(cost_verified: bool, observe_only: bool) -> bool` returns true only if costs are verified and observe-only is false.
- `enumerate_causal_sweeps(m5, config, tick_size) -> list[LiquiditySweep]`, `find_post_sweep_displacement(m5, sweep, config) -> tuple[int, float] | None`, and `find_associated_fvg(m5, sweep, displacement_index, tick_size) -> tuple[FairValueGap, int] | None` return only events confirmed within the prefix available at their confirmation time.
- `latest_bos_before(bos_events, confirmation_time) -> StructureBreak | None` chooses the last H1 close-confirmed BOS not later than the sweep reclaim; `within_development_window(bos, sweep, config) -> bool` applies the frozen 12-bar age.
- `V2Report.candidates: list[V2Setup]` and `V2Report.rejections: Counter[str]` store candidates and count every rejection reason.

- [ ] **Step 1: Add failing tests for order timing, fill side, and expiry**

```python
class PendingOrderParityTests(unittest.TestCase):
    def test_pending_order_is_not_active_before_fvg_confirmation(self):
        c3_index = 3
        c1 = Bar(300, 100.0, 101.0, 99.0, 100.5)
        c2 = Bar(600, 100.5, 102.0, 100.0, 101.8)
        c3 = Bar(900, 101.8, 103.0, 101.5, 102.8)
        gap = FairValueGap(Direction.BUY, 101.0, 101.5, c1.time, c2.time, c3.time)
        sweep = LiquiditySweep(Direction.BUY, 99.0, 98.5, 0, 300, "fixture-buy")
        candidate = V2Setup(c3_index=c3_index, order_decision_index=c3_index + 1,
                            direction=Direction.BUY, gap=gap, sweep=sweep, atr=1.0)
        self.assertEqual(candidate.order_decision_index, 4)
        self.assertGreater(candidate.order_decision_index, candidate.c3_index)

    def test_buy_limit_fills_on_ask_low_not_bid_low(self):
        bars = [make_bar(datetime.datetime(2020,1,1,10,0),100.70,100.00,100.50),
                make_bar(datetime.datetime(2020,1,1,10,5),100.70,100.00,100.40)]
        fill = pending_limit_fill(bars, Direction.BUY, 100.20, submit_index=0,
                                  pending_expiry_bars=6, pending_expiry_seconds=1800)
        self.assertIsNone(fill)  # Ask-low=100.30, so buy limit 100.20 is untouched.

    def test_sell_limit_fills_on_bid_high(self):
        bars = [make_bar(datetime.datetime(2020,1,1,10,0),100.10,99.80,100.00),
                make_bar(datetime.datetime(2020,1,1,10,5),100.21,99.90,100.10)]
        fill = pending_limit_fill(bars, Direction.SELL, 100.20, submit_index=0,
                                  pending_expiry_bars=6, pending_expiry_seconds=1800)
        self.assertEqual(fill.entry_price, 100.20)

    def test_pending_order_expires_at_six_bars_or_1800_seconds(self):
        bars = [make_bar(datetime.datetime(2020,1,1,10,0)+datetime.timedelta(minutes=5*i),
                         100.40,99.90,100.0) for i in range(6)]
        bars.append(make_bar(datetime.datetime(2020,1,1,10,30),100.60,99.90,100.50))
        fill = pending_limit_fill(bars, Direction.SELL, 100.50, submit_index=0,
                                  pending_expiry_bars=6, pending_expiry_seconds=1800)
        self.assertIsNone(fill)  # A touch at the exact expiry timestamp is too late.
```

- [ ] **Step 2: Run the focused tests and confirm they fail**

Run: `python3 -m unittest tests.test_v2_parity.PendingOrderParityTests -v`  
Expected: FAIL because pending-order modeling is not yet present.

- [ ] **Step 3: Implement the V2 order flow**

Use `research.smc_core` for confirmed BOS, pivots, displacement, and FVG detection. The V2 runner must not call the existing B3 runner or mutate its configuration. It uses this sequence:

```python
bos_events = detect_bos_sequence(h1, span=config.swing_span)
sweeps = enumerate_causal_sweeps(m5, config, manifest.tick_size)
report = V2Report()
for sweep in sweeps:  # sweep list is ordered by reclaim confirmation time
    bos = latest_bos_before(bos_events, sweep.confirmation_time)
    if bos is None or bos.direction is not sweep.direction:
        report.rejections["no_matching_confirmed_h1_bos"] += 1
        continue
    if not within_development_window(bos, sweep, config):
        report.rejections["bos_or_sweep_stale"] += 1
        continue
    displacement = find_post_sweep_displacement(m5, sweep, config)
    if displacement is None:
        report.rejections["no_post_sweep_displacement_choch"] += 1
        continue
    displacement_index, atr_value = displacement
    fvg_result = find_associated_fvg(m5, sweep, displacement_index, manifest.tick_size)
    if fvg_result is None:
        report.rejections["no_associated_three_candle_fvg"] += 1
        continue
    gap, c3_index = fvg_result

    order_index = c3_index + 1  # first bar after C3 has closed
    if order_index >= len(m5):
        report.rejections["no_post_c3_decision_bar"] += 1
        continue
    bid = m5[order_index].open
    ask = bid + m5[order_index].spread_price
    previous_day = prior_daily_range(daily_ranges, m5[order_index].server_day)
    if not premium_discount_allows(sweep.direction, bid, previous_day):
        report.rejections["v2_wrong_previous_d1_premium_discount"] += 1
        continue
    if not rolling_session_allowed(m5, order_index, config.session_lookback_bars,
                                   config.session_ratio):
        report.rejections["v2_outside_rolling_high_conviction_session"] += 1
        continue

    limit_price = midpoint(gap.lower, gap.upper)
    if not valid_limit_entry(sweep.direction, limit_price, bid, ask):
        report.rejections["limit_already_crossed_at_submission"] += 1
        continue
    candidates.append(V2Setup(c3_index, order_index, sweep.direction, gap, sweep, atr_value))
```

BUY LIMIT fills at its limit when `Bid-low + spread_price <= limit_price`; SELL LIMIT fills at its limit when `Bid-high >= limit_price`. Orders see no bars before `order_index`. Stop/TP checks use Bid for long exits and Ask=`Bid+spread` for short exits; same-bar SL/TP ambiguity resolves SL first. Commission and slippage stay separate and are reported as assumptions when not verified.

- [ ] **Step 4: Test and verify the pending model**

Run: `python3 -m unittest tests.test_v2_parity -v`  
Expected: all V2 model tests PASS; frozen B3 tests remain unchanged.

---

### Task 3: Version V2 identity and safe observe-only runtime

**Files:**
- Modify: `Include/SMC_Config.mqh`
- Modify: `Include/SMC_RuntimeConfig.mqh`
- Modify: `Include/SMC_RiskManager.mqh`
- Modify: `Experts/SMC_H1M5_SweepFVG.mq5`
- Modify: `Include/SMC_Logger.mqh`
- Modify: `tests/test_mql_static.py`

**Interfaces:**
- Runtime config adds `OBSERVE_ONLY` (default `1` in `SMC_V2.cfg` while costs are unverified).
- `SMCValidateConfiguration(..., cost_verified, observe_only, reason)` permits `cost_verified=false` only when `observe_only=true`.
- `CSMCRiskManager::Init(..., cost_verified, observe_only)` may initialize the daily reference in observe-only, but `CanOpenTrade`, `CalculateVolume`, and `CalculateNetRewardRisk` remain fail-closed without verified cost.
- `CSMCLogger.Init(..., variant, version, magic, ...)` writes `V2_PARITY` in a V2-specific CSV; frozen B3 log history remains untouched.

- [ ] **Step 1: Add failing static/runtime-config tests**

```python
class RuntimeObserveOnlyTests(unittest.TestCase):
    def test_v2_observe_only_gates_order_submission_before_submit_call(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "Experts/SMC_H1M5_SweepFVG.mq5").read_text(encoding="utf-8")
        guard = source.index("if(g_cfg_observe_only)")
        submit = source.index("SubmitMidpointLimit(", guard)
        block = source[guard:submit]
        self.assertIn('"observe_only_candidate"', block)
        self.assertIn("return;", block)

    def test_v2_has_distinct_variant_and_event_log_from_b3(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "Include/SMC_Logger.mqh").read_text(encoding="utf-8")
        self.assertIn('"V2_PARITY"', source)
        self.assertIn('"SMC_H1M5_SweepFVG_V2_events.csv"', source)
        self.assertNotIn('SMC_VERSION,"B3"', source)

    def test_cost_unverified_is_allowed_only_in_observe_only(self):
        root = Path(__file__).resolve().parents[1]
        config = (root / "Include/SMC_Config.mqh").read_text(encoding="utf-8")
        runtime = (root / "Include/SMC_RuntimeConfig.mqh").read_text(encoding="utf-8")
        self.assertIn("!cost_model_verified && !observe_only", config)
        self.assertIn("OBSERVE_ONLY", runtime)
        self.assertFalse(may_submit_order(cost_verified=False, observe_only=True))
        self.assertFalse(may_submit_order(cost_verified=False, observe_only=False))
        self.assertFalse(may_submit_order(cost_verified=True, observe_only=True))
        self.assertTrue(may_submit_order(cost_verified=True, observe_only=False))
```

- [ ] **Step 2: Run and confirm RED**

Run: `python3 -m unittest tests.test_mql_static tests.test_v2_parity.RuntimeObserveOnlyTests -v`  
Expected: new assertions fail because V2 identity and observe-only do not exist.

- [ ] **Step 3: Implement the fail-closed observe-only path**

`SMC_V2.cfg` must become:

```ini
SYMBOL=XAUUSD
MAGIC=15101602
RISK_PCT=0.25
DAILY_LOSS_PCT=2.0
COST_VERIFIED=0
OBSERVE_ONLY=1
COMMISSION_PER_LOT=0.0
SLIPPAGE_PER_LOT=10.0
LOG_LEVEL=2
```

At `WAIT_RETRACE_OR_ORDER`, preserve the daily-equity/loss and setup/filter checks, but branch to observe-only before `CanOpenTrade()`'s unverified-cost rejection. An otherwise eligible V2 setup writes one `observe_only_candidate` record with proposed limit/SL/TP and reason `transaction_costs_unverified_observe_only`, then transitions terminally so it cannot repeat each tick. It must never call `SubmitMidpointLimit`, `OrderCheck`, or `OrderSend`. Real-account blocking remains independent and unconditional.

- [ ] **Step 4: Verify static/runtime guards**

Run: `python3 -m unittest tests.test_mql_static tests.test_v2_parity.RuntimeObserveOnlyTests -v`  
Expected: PASS; zero order-submission paths are reachable in observe-only mode.

---

### Task 4: Correct final stop/target request and cache session filter

**Files:**
- Modify: `Include/SMC_Filters.mqh`
- Modify: `Include/SMC_Execution.mqh`
- Modify: `Experts/SMC_H1M5_SweepFVG.mq5`
- Modify: `tests/test_mql_static.py`

**Interfaces:**
- `SMC_ProtectionLevels(direction, entry, sweep_extreme, atr, tick_size, stop, target)` computes one final symmetric stop and 3R target.
- `SMC_V2ProtectionSelfTest() -> bool` checks a fixed BUY/SELL golden vector at init; failure blocks V2 initialization.
- `SubmitMidpointLimit` validates/rounds supplied final levels but never replaces them with the B3 raw stop.
- `SMC_SessionAllowed` reads 200 closed bars starting at shift 1; its value is cached per new M5 decision bar.
- V2 logger version/variant/magic are distinct from B3; all runtime logs use `g_cfg_*`, never raw default inputs.

- [ ] **Step 1: Add failing tests for symmetric stop and final request levels**

```python
class ProtectionParityTests(unittest.TestCase):
    def test_sell_sl_uses_sweep_high_plus_atr_and_is_widened_symmetrically(self):
        buy_sl, buy_tp = protection_levels(Direction.BUY, 100.0, 98.0, 1.0, 0.01)
        sell_sl, sell_tp = protection_levels(Direction.SELL, 100.0, 102.0, 1.0, 0.01)
        self.assertEqual(buy_sl, 80.0)   # $20 floor dominates
        self.assertEqual(sell_sl, 120.0)
        self.assertEqual(buy_tp, 160.0)
        self.assertEqual(sell_tp, 40.0)

    def test_execution_preserves_final_v2_stop_and_target(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "Include/SMC_Execution.mqh").read_text(encoding="utf-8")
        self.assertIn("SMC_ProtectionLevels", source)
        self.assertNotIn("setup.stop_loss=NormalizeDouble(SMCFloorToTick(setup.sweep_extreme", source)
        self.assertIn("request.sl=setup.stop_loss", source)
        self.assertIn("request.tp=setup.take_profit", source)

    def test_sell_raw_stop_uses_plus_atr_before_widening(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "Include/SMC_Filters.mqh").read_text(encoding="utf-8")
        self.assertIn("sweep_extreme+0.5*atr_value", source)
        self.assertIn("sweep_extreme-0.5*atr_value", source)

    def test_session_window_reads_closed_m5_bars(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "Include/SMC_Filters.mqh").read_text(encoding="utf-8")
        self.assertIn("CopyRates(symbol,PERIOD_M5,1,needed,rates)", source)
        self.assertNotIn("CopyRates(symbol,PERIOD_M5,0,needed,rates)", source)
```

- [ ] **Step 2: Run and confirm RED**

Run: `python3 -m unittest tests.test_v2_parity.ProtectionParityTests tests.test_mql_static -v`  
Expected: at least the V2 request-preservation and SELL-sign tests fail before the fix.

- [ ] **Step 3: Centralize protection calculation and session sampling**

Compute raw stop by direction, compute `distance=max(abs(entry-raw_stop), 20.0, 3.0*ATR)`, then derive `stop=entry-direction*distance` and `target=entry+direction*3.0*distance`. Round SL outward and target conservatively to tick size. Remove the duplicate raw-stop assignment from `SubmitMidpointLimit`; validate the final request values only.

For session parity, copy exactly 200 completed bars using start position 1 and cache per decision-bar timestamp. A missing/current-hour-less window fails closed. Do not call `CopyRates` for the rolling window on every tick.

- [ ] **Step 4: Compile MQL and run regression tests**

Run: `python3 -m unittest tests.test_v2_parity tests.test_mql_static -v`  
Then compile with MetaEditor build 6257:

```bash
MTE='/opt/mt5/work/wp64/drive_c/Program Files/MetaTrader 5'
WINE=/opt/wine-staging/lib/wine/x86_64-unix/wine
cd "$MTE"
timeout 150 su mt5 -c "HOME=/opt/mt5/work WINEPREFIX=/opt/mt5/work/wp64 DISPLAY=:99 WINEDEBUG=-all WINELOADER=$WINE '$WINE' ./MetaEditor64.exe /compile:./MQL5/Experts/Advisors/SMC_H1M5_SweepFVG.mq5 /log:/tmp/smc_v2_parity_compile.log"
```

Expected: 0 compile errors. Fix the existing precedence warning in `SMC_Liquidity.mqh:187` only if the parity work touches that expression; do not bundle unrelated refactors.

---

### Task 5: Python–MQL parity fixtures and no-lookahead DEV report

**Files:**
- Modify: `research/v2_parity.py`
- Modify: `tests/test_v2_parity.py`
- Create: `research/results/v2_parity_dev_20261016.json` (runner creates parent directory)
- Modify: `docs/VALIDATION_REPORT.md`
- Modify: `AGENTS.md`

**Interfaces:**
- `run_v2_exploratory(m5_path, h1_path, export_manifest_path, config, dev_start, dev_end, output_path) -> dict` writes an explicitly `EXPLORATORY_BAR_MODEL`, `oos_claim_permitted=false` report.
- Report records raw file hashes, available account/server/symbol metadata, frozen V2 configuration, per-filter rejections, pending fills/expiry, `commission_status=UNVERIFIED`, slippage assumptions, and `acceptance_status=INCONCLUSIVE` because no untouched OOS remains.
- Any corrected DEV metric is diagnostic/selection-affected; do not label it out-of-sample or use the burned interval.

- [ ] **Step 1: Write failing parity tests**

```python
class V2ParityTests(unittest.TestCase):
    def test_protection_golden_values_match_the_mql_v2_self_test(self):
        root = Path(__file__).resolve().parents[1]
        mql = (root / "Include/SMC_Filters.mqh").read_text(encoding="utf-8")
        # Shared golden vector: tick=.01, floor=20, ATR multiple=3, target=3R.
        buy_sl, buy_tp = protection_levels(Direction.BUY, 2000.0, 1995.0, 2.0, 0.01)
        sell_sl, sell_tp = protection_levels(Direction.SELL, 2000.0, 2005.0, 2.0, 0.01)
        self.assertEqual((buy_sl, buy_tp), (1980.0, 2060.0))
        self.assertEqual((sell_sl, sell_tp), (2020.0, 1940.0))
        self.assertIn("SMC_V2ProtectionSelfTest", mql)
        for price in ("1980.0", "2060.0", "2020.0", "1940.0"):
            self.assertIn(price, mql)

    def test_v2_decision_is_invariant_to_appending_future_same_day_bars(self):
        decision_time = datetime.datetime(2020, 1, 2, 10, 0)
        before = [make_bar(datetime.datetime(2020,1,1,23,55),120,80,100),
                  make_bar(decision_time,100,90,95)]
        after = before + [make_bar(datetime.datetime(2020,1,2,15,0),150,80,120)]
        day = decision_time.date()
        decision_before = premium_discount_allows(
            Direction.BUY,95.0,prior_daily_range(aggregate_daily_ranges(before),day)
        )
        decision_after = premium_discount_allows(
            Direction.BUY,95.0,prior_daily_range(aggregate_daily_ranges(after),day)
        )
        self.assertEqual(decision_before, decision_after)

    def test_pending_fill_cannot_use_a_bar_before_order_submission(self):
        c3 = make_bar(datetime.datetime(2020,1,1,10,0),101.0,99.0,100.0)
        decision = make_bar(datetime.datetime(2020,1,1,10,5),100.4,99.8,100.0)
        later = make_bar(datetime.datetime(2020,1,1,10,10),100.6,99.0,100.4)
        bars = [c3, decision, later]
        fill = pending_limit_fill(bars,Direction.SELL,100.5,submit_index=1,
                                  pending_expiry_bars=6,pending_expiry_seconds=1800)
        self.assertEqual(fill.entry_index,2)
```

- [ ] **Step 2: Run and confirm RED**

Run: `python3 -m unittest tests.test_v2_parity -v`  
Expected: the new parity/no-lookahead tests fail until the V2 runner and fixtures are implemented.

- [ ] **Step 3: Implement V2-only runner and evidence report**

Keep `research/experiment.py` and frozen B3 results unchanged. V2 reads only M5 bars from 2018-07-01 and matching H1 bars. DEV ends at `2024-04-23 18:05` broker time; the burned 2024-04..2026-10 interval is never loaded by this runner. Costs remain separated; with unverified commission/slippage, report zero-cost structure and scenario costs as assumptions, not verified broker costs.

- [ ] **Step 4: Run targeted + full tests, then DEV-only study**

Run (paths quoted because `/root/BBMA OMA ALI` contains spaces):

```bash
python3 -m unittest tests.test_v2_parity -v
python3 -m unittest discover -s tests -v
python3 -m research.v2_parity \
  --m5-path "/root/BBMA OMA ALI/data/raw/XAUUSD_HFM_M5_20261015_022415_482518984.csv" \
  --h1-path "/root/BBMA OMA ALI/data/raw/XAUUSD_HFM_H1_20261015_022415_482518984.csv" \
  --export-manifest "/root/BBMA OMA ALI/data/raw/XAUUSD_HFM_manifest_20261015_022415_482518984.txt" \
  --dev-start "2018-07-01 00:00:00" --dev-end "2024-04-23 18:05:00" \
  --output "research/results/v2_parity_dev_20261016.json"
```

Expected: all tests pass; report states `EXPLORATORY_BAR_MODEL`, `oos_claim_permitted=false`, and `acceptance_status=INCONCLUSIVE` because this DEV-only diagnostic cannot satisfy an untouched-OOS gate regardless of its metrics.

---

### Task 6: Deploy V2 observe-only and verify operation

**Files:**
- Modify: `/etc/mt5-ea.env` or `MQL5/Files/SMC_V2.cfg` (root-only runtime config; no credentials)
- Modify: `/usr/local/bin/mt5-chart-patch.py` only if it must create/retain the distinct V2 chart/log identity
- Create/Update: `MQL5/Files/SMC_H1M5_SweepFVG_V2_events.csv` via EA

**Interfaces:**
- Demo4 remains on chart02 M5, guard stub remains on chart01 H1.
- Runtime config explicitly has V2 identity, risk 0.25%, daily cap 2%, `COST_VERIFIED=0`, `OBSERVE_ONLY=1`.
- One `m5_bar_processed` event per new closed M5 bar reports config hash, strategy state, filters, rejection reason, spread, demo server, and processed bar timestamp. Other signal/observe-only events go to the same V2-only CSV.

- [ ] **Step 1: Test the runtime config contract**

Run: `python3 -m unittest tests.test_mql_static tests.test_v2_parity.RuntimeObserveOnlyTests -v`. Assert `OBSERVE_ONLY=1` and `COST_VERIFIED=0` log `observe_only_candidate`, advance state/health, and cannot call `SubmitMidpointLimit`, `OrderCheck`, or `OrderSend`.

- [ ] **Step 2: Compile and deploy**

Compile the final EA (0 errors), copy only the V2 `.ex5` and required `SMC_*.mqh` includes into MT5, preserve chart01 guard, restart `mt5-terminal.service`, and verify `NRestarts=0`.

- [ ] **Step 3: Verify runtime behavior**

Check account mode is Demo4, real-account block remains active, runtime risk=0.25%, cost flag false, observe-only true, guard heartbeat advances, V2 status advances on closed M5 bars, no pending orders/open positions are created, and B3/V1 event files are untouched.

---

## Self-review

- **Coverage:** no-lookahead D1, session parity, pending midpoint fill/expiry, BUY/SELL stop symmetry, final request preservation, costs, V2 identity, observe-only, telemetry, test/report/deploy are each assigned to a task.
- **Frozen B3:** no B3 rule/threshold/spec is altered; the experimental V2 has a separate identity and report.
- **OOS integrity:** the previously viewed 2024-04..2026-10 interval is excluded from all V2 selection and acceptance.
- **Placeholder scan:** no TBD/TODO or unspecified threshold. Runtime constants are copied from the V2 candidate or approved constraints.
- **Cost truthfulness:** `COST_VERIFIED=1` is not allowed until commission/slippage are actually verified; until then, V2 is observe-only.
