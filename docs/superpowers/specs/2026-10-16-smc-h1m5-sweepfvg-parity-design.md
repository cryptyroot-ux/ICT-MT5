# SMC-H1M5-SweepFVG No-Lookahead Parity and Runtime Health Design

**Date:** 2026-10-16  
**Status:** User-approved parity-first design (2026-10-16)  
**Branch:** `feature/smc-h1m5-sweepfvg`

**Experiment identity:** This is an experimental **V2** candidate. It is not B3 and must not overwrite the frozen B3 specification.

## Goal

Make the Python research model and the running `SMC_H1M5_SweepFVG` EA implement the same frozen behavior, remove lookahead from the premium/discount evaluation, correct the BUY/SELL stop asymmetry, and provide enough runtime telemetry to distinguish a healthy no-signal state from a stalled EA.

This work is about **correctness, parity, and observability**. It does not claim or tune profitability.

## Evidence motivating the change

1. Python V2 aggregated the entire current server-calendar day's M5 highs/lows, then used that final daily range to classify intraday setup prices. That leaks future bars.
2. The EA uses the prior completed D1 bar (`CopyRates(PERIOD_D1, 1, 1, ...)`) for premium/discount.
3. On the same DEV window and frozen V1 setup parameters, replacing same-day D1 with prior-completed-D1 changed the exploratory result from 111 setups / `+0.1722R` zero-cost to 26 setups / `-0.1321R` zero-cost. The previous positive V2 figure is invalid evidence.
4. Python V2 froze session hours `[15,16,17,18]` from the entire DEV period; the EA instead recalculates a volatility threshold from the latest 200 M5 bars. These are different strategies.
5. EA SELL stop distance currently uses `sweep_extreme - 0.5 * ATR`, the BUY-side formula. SELL requires `sweep_extreme + 0.5 * ATR`.
6. The EA places a pending limit at the FVG midpoint and expires it at the earlier of 6 completed M5 bars or 30 minutes. The Python V1/V2 harness models a market entry at an M5 bar open instead.
7. The EA orchestration assigns the V2 widened SL, but `SubmitMidpointLimit()` recalculates and overwrites `setup.stop_loss` from sweep extreme ±0.5 ATR. Thus the configured V2 floor does not reach the broker request. The final request values, not an intermediate field, must be the tested contract.
8. Branch documentation still describes frozen B3 without PD/session filters or fixed 3R. The running EA contains those V2 changes and its event CSV currently labels events `B3`; this makes experiment identity ambiguous.
9. `SMC_V2.cfg` currently sets `COST_VERIFIED=1`, while round-turn commission is set to zero and slippage `$10/lot` is an assumed scenario, not yet verified from this account's fee schedule and actual fills. The flag currently overstates the evidence.
10. `ManagePendingSetup()` is called every tick before the new-M5-bar guard. It can submit a V2 pending order partway through the bar, while the bar model can only place it at a known boundary. V2 must make the decision at the first tick of the M5 bar after C3 close (then the bar-model uses that bar's open).
11. The FVG branch returns immediately after the C3 transition to `WAIT_RETRACE_OR_ORDER`; `ProcessSMC()` therefore does not reach the pending-order branch until the next M5 bar closes. That delays submission one extra candle beyond the C3-close reference. The fix must let the same new-bar invocation continue to the order-decision branch.

## Source of truth and frozen constraints

- Frozen B3 in `docs/SMC_H1M5_SWEEPFVG_SPEC.md` remains unchanged and is the historical baseline.
- The currently running code is V2 experimental: it adds prior-D1 premium/discount, a session filter, a stop floor, and fixed 3R instead of next-liquidity target. V2 receives its own explicit spec, version/variant label, magic number, config hash, and event-log identity so it cannot be mistaken for B3.
- The MQL5 V2 EA's intended behavior, corrected by this design, is the implementation-parity target for this phase. If behavior is ambiguous, add a test or explicit rejection; do not silently guess.
- Historical XAUUSD data is bar-based M5/H1 with per-bar spread snapshots, **not tick-accurate**.
- Eligible historical research starts `2018-07-01`; raw export CSVs remain read-only.
- The historical 2024-04..2026-10 window is burned and cannot be used for a fresh OOS claim.
- Risk remains 0.25% on the Demo4 forward test; 2% daily loss cap; real accounts stay hard-blocked.
- Until commission/fees and execution slippage are verified, runtime config must set cost verification false and the EA must run **observe-only**: process bars, filters and setups, write health/setup/rejection events, but never submit a pending order.
- Existing acceptance gates remain frozen: at least 100 completed trades, expectancy ≥ `+0.25R`, PF ≥ `1.50`, max drawdown < `12%`, plus cost stress. A miss is FAIL or INCONCLUSIVE, never a relaxed pass.
- No parameter search, window shift, new threshold, or claimed profitability is included in this parity repair.
- The V2 constants already present in the running candidate are frozen for this repair; this work only makes them causal, symmetric, observable, and consistent across Python/MQL.
- Preserve B3 as a separate immutable comparator. V2 gets a distinct version, variant name, magic number and event file; the old B3 spec and its history are not silently redefined.

## Proposed behavior contract

### 1. Premium/discount without lookahead

- At the EA's order-submission decision, use only the **previous fully closed D1 bar** high/low.
- BUY eligibility is the observable Bid at the order-decision time strictly below that prior D1 midpoint; SELL eligibility is Bid strictly above it; exact midpoint is neutral/rejected.
- Python builds D1 bars from M5 only for closed prior server-days, then joins the prior available trading-day range to the order-decision timestamp. For the bar model, the first price after C3 close is the next M5 bar open; never use current-day final high/low.
- Tests include a day whose later high/low would change the classification, proving an earlier timestamp remains unchanged.

### 2. Session filter parity

- Keep the active V2 session rule semantics for this parity pass: hourly mean of M5 `high-low` over the latest 200 **closed** M5 bars; an order decision passes when the current server hour's measured mean is at least `1.30 ×` the mean over hours with observations.
- Change MQL to exclude the forming M5 bar and compute/cache the value at each new-M5-bar order-decision boundary, not repeatedly over mutable bar data on every tick.
- Python computes the identical rolling window ending at the last closed M5 bar before order submission. Insufficient history or no observations for the current hour fails closed.
- Do not reuse the previously mined fixed `[15,16,17,18]` session list; it was selected over the full DEV sample and does not match the running EA.

### 3. Setup-to-pending-order parity

- A setup is formed only after the frozen closed-bar sequence: H1 BOS bias → M5 sweep/reclaim → post-sweep displacement/CHoCH → associated 3-candle FVG.
- When the FVG is confirmed at C3 close, the EA evaluates risk/session/prior-D1 filters and submits the pending limit on the **first tick of the immediately following M5 bar**. Remove the early return after the FVG transition so `WAIT_RETRACE_OR_ORDER` runs on that same new-bar invocation. The Python model uses that bar's open, checks that the limit is still on the valid side of bid/ask, then activates the pending order.
- The pending order can fill only after submission and expires at the earlier of 6 completed M5 bars or 30 minutes.
- With Bid OHLC and per-bar spread snapshot: approximate BUY-limit touch using Ask-low (`Bid-low + spread`) and SELL-limit touch using Bid-high. The decision/open bar is the first post-submission bar; no pre-order movement or C3 range may fill the pending order. State this bar-model approximation in every report.
- After fill, a long's SL/TP trigger uses Bid OHLC; a short's SL/TP trigger uses Ask OHLC approximated as Bid OHLC plus that bar's spread. If both SL and TP are touched within one bar, resolve SL first.
- No market fill at the open of the first retrace bar; no fill using the signal bar's future range.

### 4. Stops, targets, costs, and risk

- Raw BUY stop: `sweep_low - 0.5 × completed M5 ATR(14)`; raw SELL stop: `sweep_high + 0.5 × completed M5 ATR(14)`.
- Widen distance symmetrically to `max(abs(planned entry - raw stop), $20/oz, 3 × ATR)` and round away from the planned limit entry to the broker tick size.
- Define one stop/target calculation path. `SubmitMidpointLimit()` must not silently overwrite the widened stop/target; either it owns the complete calculation or it validates/rounds the supplied levels unchanged. Tests inspect the final pending `MqlTradeRequest.sl/tp` values for both BUY and SELL.
- Fixed target is 3R from the **planned pending-limit entry** using the widened stop distance. Realized R uses the actual broker fill. Compute minimum net RR at submission after modeled spread, commission, fees, and slippage; fail closed if net RR < 3.
- Spread, commission, slippage, and stop buffer are separate quantities. Entry/exit side effects must match MQL Bid/Ask rules; never charge spread twice.
- Broker-specific tick value, contract size, minimum volume, volume step, margin, and daily equity reference continue to come from runtime symbol/account metadata.
- `COST_VERIFIED=1` is allowed only when the relevant commission/fee schedule is confirmed for the active account and execution slippage is calibrated or explicitly approved as a conservative verified model. An arbitrary nonnegative slippage input does not satisfy this gate.

### 5. Runtime health and observability

- Preserve guard stub on chart01 and SMC EA on chart02, Demo4-only real-account block, 0.25% risk, and 2% daily cap.
- Emit one bounded health/status record per newly closed M5 bar (not per tick), containing timestamp, build/config hash, symbol/server/account mode, risk config, spread, strategy state, last transition/rejection reason, and whether the closed bar was processed.
- Add an explicit `OBSERVE_ONLY` runtime switch. `OBSERVE_ONLY=1` permits initialization and signal/event telemetry but blocks every `OrderSend`/pending order. `OBSERVE_ONLY=0` is rejected unless `COST_VERIFIED=1` and all risk/config validation passes.
- Do not emit order/fill claims from a submitted request alone; keep actual deal lifecycle accounting as the source of realized trades.
- A no-signal interval is healthy only if bar-processing telemetry advances and the state/rejection reason is explainable.

## Data flow

```text
HFM raw M5/H1 bars (read-only)
  -> causal feature windows (closed bars only)
  -> H1 bias / M5 sweep-displacement-CHoCH-FVG
  -> order-time prior-D1 PD + rolling session filter
  -> midpoint pending order, 6 bars / 30 min expiry
  -> Bid/Ask-aware bar-model fill, symmetric ATR/floor SL, 3R TP
  -> spread/commission/slippage-separated accounting
  -> DEV report + parity contract checks

MQL5 EA:
  closed M5 bar -> same filters/levels -> pending limit -> broker reconciliation
  -> event CSV + per-M5 health record
```

## Validation and acceptance

1. Unit tests prove: prior-D1 join never reads same-day future extrema; BUY/SELL stops are symmetric; pending fills occur only after submission and expire at the earlier boundary; session rolling window excludes the forming candle; spread/slippage accounting is side-correct.
2. A deterministic parity fixture feeds the same bars/config through Python and MQL pure-helper checks and compares setup/order levels within one broker tick.
3. Python full suite and MQL MetaEditor compile must pass before deployment.
4. Re-run the corrected DEV-only study with frozen settings; do not read burned OOS for tuning or acceptance.
5. Observe-only verification: with `OBSERVE_ONLY=1` and `COST_VERIFIED=0`, several new M5 bars are processed and logged, while order/pending-order count remains zero. Add a regression/static test proving this switch cannot call order submission.
6. Demo runtime verification: service active, no restarts, heartbeat and per-M5 status advance, runtime config shows 0.25%, server is Demo4, no real-mode path, and actual order/deal state reconciles.
7. Corrected DEV is diagnostic only: even if every numeric threshold is met on DEV, the result is still **INCONCLUSIVE**, not a pass. Report misses plainly. No promotion or performance claim is allowed until fresh forward data is collected; keep risk at 0.25% demo.

## Documentation / identity requirements

- Keep `docs/SMC_H1M5_SWEEPFVG_SPEC.md` describing frozen B3. Add a separate V2 spec and update `AGENTS.md` to mark the V2 demo candidate as experimental, not a replacement of frozen B3.
- Event records must not call the V2 strategy `B3`. Use a V2-specific variant/version, unique magic number, and segregated event-log filename/config hash. Existing B3 history must remain untouched.
- Validation report must withdraw the leaky V2 result (`+0.135R`, PF 2.34) and record the prior-D1 diagnostic as exploratory only, with the runtime/session/parity caveats.

## Non-goals

- No search for a new profitable strategy or changes to frozen SMC signal definitions in this parity pass.
- No use of the burned 2024-04..2026-10 OOS for selecting parameters or declaring success.
- No live-account deployment or removal of real-account safeguards.
- No assumption that the bar model reproduces tick-level intrabar ordering.

## Implementation units

1. Python causal D1/session/execution model and regression tests.
2. MQL SELL stop correction, closed-bar session cache, and bounded per-M5 health event.
3. Parity/validation report generation and DEV-only rerun.
4. Demo deployment and operational check against the status contract above.
