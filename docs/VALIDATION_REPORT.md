# Validation Report — SMC-H1M5-SweepFVG

**Date:** 2026-10-15  
**Branch:** `feature/smc-h1m5-sweepfvg`  
**Base commit (upstream `main`):** `e1d1a80bee2454ef507f89c69256036a0a97dd5e`  
**Readiness:** **RESEARCH / DEMO ONLY** — not live-ready, and out-of-sample performance
has not been measured.

This report distinguishes three kinds of evidence and never mixes them:

- **Executed here** — I ran the command in this environment and quote the result.
- **NOT VERIFIED** — the required environment/toolchain/data was unavailable.
- **INCONCLUSIVE** — evidence exists but cannot support a pass/fail decision.

## 1. Build and compilation

| Item | Status | Evidence |
|---|---|---|
| Python 3.12 syntax check of all `.py` | PASS (executed) | `py_compile` over `backtester/`, `docs/`, `scripts/`, `research/`, `tests/` returned no output |
| Deterministic logic tests | PASS (executed) | `python3 -m unittest discover -s tests` → `Ran 54 tests ... OK` |
| MQL5 static structure checks | PASS (executed) | `tests/test_mql_static.py` (brace/paren balance, include resolution, guard pairing, logger arity, identifier fields, MT4-API leakage) |
| MetaEditor compile of `SMC_H1M5_SweepFVG.mq5` | **NOT VERIFIED** | No `metaeditor64.exe` found on this host; Wine and `terminal64.exe`/`metatester64.exe` exist under `/opt/mt5`, but a filename search for a MetaEditor binary returned nothing |
| Strategy Tester run (real ticks) | **NOT VERIFIED** | Requires a logged-in HFM Pro demo account and the compiled EA |
| Strategy Tester run (bar model) | **NOT VERIFIED** | Same blockers |

**Mutation check on the static tests.** To confirm the static suite is not vacuous,
three deliberate defects were injected and each was caught, then reverted:
dropping a required logger argument at a call site → FAILED; unbalancing a brace in a
module → FAILED; removing an include-guard `#define` → FAILED. After restoring, the
suite returned to `OK`.

**Two test-expectation bugs were found and fixed during this work** (fixtures/expected
values, not production defects): an H1 aggregation fixture that used 24 M5 bars (two
hours) while asserting one hour, and an all-winning trade set whose profit factor is
infinite rather than failing a profit-factor gate.

## 2. Strategy compliance

Assessed against `docs/SMC_H1M5_SWEEPFVG_SPEC.md`. "Implemented" means present in the
committed MQL5 source and exercised by the deterministic reference tests; runtime
behavior inside MetaTrader 5 is separately marked NOT VERIFIED.

| Required stage | Status | Where |
|---|---|---|
| H1 confirmed BOS bias | Implemented; runtime NOT VERIFIED | `Include/SMC_MarketStructure.mqh` (close-only BOS over span-2 confirmed pivots) |
| M5 sweep and reclaim | Implemented; runtime NOT VERIFIED | `Include/SMC_Liquidity.mqh` (`DetectSweep`) |
| Post-sweep displacement | Implemented; runtime NOT VERIFIED | `Include/SMC_Displacement.mqh` (body ≥ 1×ATR, ratio ≥ 0.60, outer quartile close) |
| Post-sweep CHoCH/MSS | Implemented; runtime NOT VERIFIED | `Include/SMC_Displacement.mqh` (internal pivot formed after reclaim, broken by close) |
| Three-candle FVG | Implemented; runtime NOT VERIFIED | `Include/SMC_FVG.mqh` (C2 = displacement, chronological C1/C2/C3) |
| Retracement limit entry | Implemented; runtime NOT VERIFIED | `Include/SMC_Execution.mqh` (`TRADE_ACTION_PENDING`, FVG midpoint) |
| Sweep-extreme SL + 0.5×M5 ATR(14) | Implemented; runtime NOT VERIFIED | `SMC_Execution.mqh` (outward tick rounding) |
| Next-liquidity target | Implemented; runtime NOT VERIFIED | `SMC_Liquidity.mqh` (`FindNextLiquidity`) |
| Minimum net R:R 1:3 | Implemented; runtime NOT VERIFIED | `SMC_RiskManager.mqh` (`CalculateNetRewardRisk`, cost-inclusive) |
| Pending-order expiry (6 bars / 30 min) | Implemented; runtime NOT VERIFIED | `SMC_Execution.mqh` + EA `ManagePendingSetup` (earlier of both) |
| Setup invalidation | Implemented; runtime NOT VERIFIED | `SMC_SetupStateMachine.mqh`, EA `InvalidateCurrentSetup` |
| Risk limits | Implemented; runtime NOT VERIFIED | `SMC_RiskManager.mqh` (stable daily reference, 2 % cap, two-loss streak) |
| Actual transaction accounting | Implemented; runtime NOT VERIFIED | `SMC_TradeLedger.mqh` (position lifecycle by `DEAL_POSITION_ID`, actual profit/commission/swap/fee) |
| Real-account blocking | Implemented | `OnInit` returns `INIT_FAILED` on `ACCOUNT_TRADE_MODE_REAL` |

## 3. Deterministic test coverage

26 reference-logic tests (`tests/test_smc_core.py`) cover: swing confirmation requiring
two completed right bars; unfinished H1 candle unable to confirm a pivot or BOS;
bullish BOS only on a completed close; bearish BOS; sweep excursion/reclaim ordering and
stable setup ID; sweep without reclaim; reclaim without threshold sweep; stale
liquidity level; displacement body/ratio/close-location gates; completed-bar SMA ATR(14)
and refusal to sample an unfinished bar; bullish and bearish three-candle gaps; no gap;
sub-tick gap; pre-sweep and unrelated displacement rejection; unfinished C3; FVG
fresh/partial/consumed/invalidated classification; state-machine skip/terminal refusal;
midpoint and pending expiry; volume flooring to step with below-minimum rejection and
max cap; invalid tick values; tick-vs-`OrderCalcProfit` agreement and disagreement
rejection; net R:R cost treatment; daily cap boundary at 2 %; consecutive-loss counting
and reset; limit-order side validity; actual position-lifecycle PnL over partial exits
with foreign-magic and foreign-position deals excluded.

20 evidence/runner tests (`tests/test_experiment.py`) cover: manifest hash mismatch,
synthetic/third-party/bar-model refusal for OOS, exploratory bar-model allowance,
invalid broker properties, non-XAUUSD refusal, duplicate/unsorted timestamps, H1
aggregation ignoring incomplete hours, chronological disjoint splits, boundary purging,
metric derivation from actual trades, INCONCLUSIVE below 100 trades even when metrics
look strong, FAIL with a sufficient sample and weak metrics, PASS only when every
threshold is met, CI absent for tiny samples, CSV round-trip, and B0 refusal.

## 4. Quantitative results

| Variant | Trade count | Expectancy | Profit factor | Max DD | Win rate | Costs | OOS | CI | Stress |
|---|---|---|---|---|---|---|---|---|---|
| B0 | — | — | — | — | — | — | — | — | — |
| B1 | — | — | — | — | — | — | — | — | — |
| B2 | — | — | — | — | — | — | — | — | — |
| B3 | — | — | — | — | — | — | — | — | — |

All cells are **NOT VERIFIED / INCONCLUSIVE**. No B0–B3 run was executed, for two
independent reasons:

1. No HFM Pro account or verified symbol specification was available, so no broker tick
   export exists and `research/experiment.py` refuses the run at the manifest gate.
2. B0 cannot be produced by the Python harness at all. The legacy EA does not compile as
   received (AUD-001) and is preserved unmodified; re-implementing it in Python would
   not be "the original baseline". B0 requires a documented technical repair plus a
   MetaEditor/Strategy Tester run.

The checked-in upstream Python artifacts (`docs/*_trades.csv`, `stress_test_summary.csv`,
`equity_curve.csv`, the Demo GIF) are **not** evidence for any row above. They contain
synthetic generated prices, Yahoo `GC=F` gold futures, a seeded random outcome draw for
NAS100/US30, fixed ±1R/3R PnL regardless of the actual exit distance, missing
transaction costs, and look-ahead reuse of full-sample state — see AUD-012.

## 5. Acceptance gates

Thresholds are reproduced exactly as frozen before any results were viewed. They were
not relaxed, reinterpreted, or averaged.

| Gate | Threshold | Measured | Status |
|---|---|---|---|
| Net expectancy | ≥ +0.25R/trade | not measured | **INCONCLUSIVE** |
| Profit factor | ≥ 1.50 | not measured | **INCONCLUSIVE** |
| Max equity drawdown | < 12 % | not measured | **INCONCLUSIVE** |
| Completed trades | ≥ 100 | 0 | **INCONCLUSIVE** |
| Mandatory risk limits correct | all correct | not runtime-verified | **NOT VERIFIED** |
| No unresolved critical execution/accounting defects | none | see §6 | **NOT VERIFIED** |

A sample-size shortfall is INCONCLUSIVE, not a pass. No metric was concealed by
averaging.

## 6. Unresolved issues and environmental limitations

1. **Compilation unverified.** No MetaEditor binary on this host. The MQL5 sources are
   statically checked only (structure, includes, guards, logger arity, identifier
   separation). Static checks do not prove the EA compiles, and compilation would not
   prove the strategy works.
2. **Broker specification unverified.** Contract size, tick value, volume step, stops/
   freeze level, filling mode, commission, and swap for the target HFM Pro symbol are
   unknown. The code reads them at runtime and fails closed, but no measured capture
   exists.
3. **No historical tick evidence.** Prior separate work on HFM **Demo4** (not Pro) found
   bar history back to 2017-10-11 but six of six historic one-hour `CopyTicksRange`
   windows returned `NO_DATA` while a recent control succeeded. Multi-year Bid/Ask tick
   history is therefore not demonstrated.
4. **Loss streak scope decision.** The streak is reconstructed from the current broker
   server day's EA-owned completed lifecycles, so entries resume next server day. If
   intended to span days, that is a spec revision, not a silent change.
5. **Daily reference failure mode.** If the persisted daily reference is lost mid-day,
   the EA fails closed instead of re-baselining. This is deliberate but unverified at
   runtime.
6. **Baseline comparison is not yet possible** for reason (2) above.
7. **Python reference vs MQL5 divergence risk.** The Python model in
   `research/smc_core.py` mirrors the frozen rules, but it is not the EA. Divergence
   between the two has not been machine-verified; the MQL5 EA is the authority for
   trading and the Python model is only for fixtures and data gating.
8. **Strategy Tester visual/behavioral review** of the EA has not occurred.

## 7. What must happen before demo or live consideration

1. Compile in MetaEditor with the real build number recorded; fix all errors and
   investigate warnings.
2. Capture `Scripts/SMC_SymbolSpec.mq5` output on HFM Pro demo and fill in
   `docs/BROKER_CONFIGURATION.md` §2.
3. Supply measured commission/fee and a conservative slippage allowance; only then set
   `InpSMCCostModelVerified=true`.
4. Run the Strategy Tester on real ticks across chronological in-sample / validation /
   untouched out-of-sample splits, with boundary purging.
5. Execute B1/B2/B3 under the same data, risk, and cost assumptions; document any
   unavoidable difference.
6. Repair and run B0 in the Strategy Tester so each added component's contribution is
   actually measurable.
7. Report every frozen gate as PASS / FAIL / INCONCLUSIVE without adjusting thresholds.
8. Re-review the full diff and obtain independent review before any live consideration.

**No profitability claim is made. This project is RESEARCH / DEMO ONLY.**
