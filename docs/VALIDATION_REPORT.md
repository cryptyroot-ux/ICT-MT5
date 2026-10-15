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

### 4.1 Evidence actually available

A real HFM bar export exists on the authoring host (M5 591,777 rows 2017-10-17 →
2026-10-15; H1 53,028 rows 2017-10-11 → 2026-10-15; sha256-verified in the run
manifest). It was used for an **exploratory bar-model** analysis. Its limitations are
material and are listed in §4.3.

| Property of that dataset | Value |
|---|---|
| Server | `HFMarketsGlobal-Demo4` |
| Account | `#Centz - Ari Susanto` — a **Cent** account, currency **IDR** |
| Target account | **HFM Pro — not this account** |
| Data type | broker **bar** export; per-bar `spread_points` is 0 in older segments |
| Historical Bid/Ask ticks | not available (six of six historic 1-hour `CopyTicksRange` windows returned `NO_DATA`; only a recent control worked) |
| Commission / fees | **not measured** |
| Slippage | **not measured** |

`research/experiment.py` classifies this as `EXPLORATORY_BAR_MODEL` with
`oos_claim_permitted = false` and refuses it for any out-of-sample claim.

### 4.2 The decisive finding: the frozen rules are mutually near-unsatisfiable

With the strategy implemented exactly as frozen — stop at the sweep extreme ± 0.5 ×
M5 ATR(14), target at the **next opposing confirmed liquidity level**, acceptance
requiring **net R:R ≥ 3.00** — the two rules are in severe tension on this data.
Measured over 984 qualifying OOS sweep events:

| Raw R multiple of next-liquidity target vs sweep-extreme stop | Value |
|---|---|
| sample | 984 |
| minimum | 0.00 |
| p10 / p25 | 0.01 / 0.03 |
| **median** | **0.06** |
| p75 / p90 / p99 | 0.16 / 0.45 / 1.39 |
| maximum | 3.63 |
| share with raw R ≥ 3.0 | **0.10 %** |
| share with raw R ≥ 6.0 | 0.00 % |
| median stop distance | 1.718 price units |

The nearest opposing liquidity level is typically only ~6 % of the stop distance away.
Because net R:R is `(reward − cost) / (risk + cost)`, requiring net ≥ 3 forces
`reward ≥ 3·risk + 4·cost`, so any positive transaction cost makes acceptance strictly
harder. Consequence, measured on the OOS segment:

| Cost charged per lot | B1 | B2 | B3 |
|---|---:|---:|---:|
| none (unmeasured baseline) | 1 trade | 1 trade | 2 trades |
| median broker spread × 2 crossings | 0 | 0 | 0 |
| 10× median × 2 | 0 | 0 | 0 |
| 100× median × 2 | 0 | 0 | 0 |

**Therefore B0, B1, B2, and B3 as literally specified produce statistically
insignificant trade counts on this data, and every frozen acceptance gate is
INCONCLUSIVE.** This is a property of the specification, not of the implementation: the
code faithfully implements what the specification says.

> **Correction of an earlier intermediate result.** An earlier harness revision
> manufactured a target at exactly 3.0 × the stop distance, which by construction can
> never satisfy the net-cost gate and produced ~554 apparently strong trades. That run
> measured a *different* strategy than the frozen specification and is **not** reported
> as B3 evidence. It is superseded by this section.

### 4.3 Variant metrics

| Variant | Trade count | Expectancy | Profit factor | Max DD | Win rate | Costs | OOS | CI | Stress |
|---|---|---|---|---|---|---|---|---|---|
| B0 | not run | — | — | — | — | — | — | — | — |
| B1 | 1 (OOS) | n/a | n/a | 0.00 % | 100 % (n=1) | unmeasured | not claimed | n/a | collapses to 0 with any cost |
| B2 | 1 (OOS) | n/a | n/a | 0.00 % | 100 % (n=1) | unmeasured | not claimed | n/a | collapses to 0 with any cost |
| B3 | 2 (OOS) | n/a | n/a | 0.00 % | 100 % (n=2) | unmeasured | not claimed | n/a | collapses to 0 with any cost |

B0 could not be run at all: the legacy EA does not compile as received (AUD-001) and
is preserved unmodified, so re-implementing it in Python would not be "the original
baseline".

The checked-in upstream Python artifacts remain **not** evidence: synthetic prices,
Yahoo `GC=F`, a seeded random outcome draw, fixed ±1R/3R PnL regardless of exit
distance, missing costs, and look-ahead reuse of full-sample state (AUD-012).

### 4.4 What was measured and found sound

- Within-bar stop/target ambiguity (both levels inside the same bar, so ordering is
  unknowable from OHLC) is **negligible** on this data: 0.00 %–0.22 % of trades per
  variant/segment. This bias does not materially inflate the numbers above.
- The BOS replay produces 5,037 structural break events across the 9-year H1 series,
  with both bullish and bearish breaks present, so signal scarcity is caused by the
  target/R:R rules rather than by a broken structure detector.

## 5. Acceptance gates

Thresholds are reproduced exactly as frozen before any results were viewed. They were
not relaxed, reinterpreted, or averaged.

| Gate | Threshold | Measured | Status |
|---|---|---|---|
| Net expectancy | ≥ +0.25R/trade | not measurable (B3 OOS n = 2) | **INCONCLUSIVE** |
| Profit factor | ≥ 1.50 | not measurable (B3 OOS n = 2) | **INCONCLUSIVE** |
| Max equity drawdown | < 12 % | not measurable (B3 OOS n = 2) | **INCONCLUSIVE** |
| Completed trades | ≥ 100 | B3 OOS = 2 | **INCONCLUSIVE** |
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
9. **Specification tension (highest-priority finding, §4.2).** The frozen stop rule
   (sweep extreme ± 0.5 × M5 ATR(14)) combined with the frozen target rule (next
   opposing liquidity) yields a median raw R of only 0.06, and 0.10 % of events reach
   R ≥ 3. As specified, the strategy is near-untradeable. This must be resolved by an
   explicit specification decision, not by adjusting an acceptance threshold. Candidate
   resolutions, none of which may be applied without a new documented version:
   (a) widen the stop budget so the risk unit is smaller relative to the next liquidity
   distance; (b) define the target as the next *significant* opposing liquidity level
   rather than the immediately nearest pivot; (c) lower the net R:R requirement and
   re-freeze it before any out-of-sample evaluation. Any of these changes the strategy
   definition and therefore invalidates the current freeze.

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

**Bottom line:** the engineering work (audit, EA, risk control, accounting, tests) is
implemented and statically verified. The *strategy as frozen* is, however, near-untradeable
on the only real data available, so there is nothing to declare profitable — and the
blocking issue is a specification decision, not an implementation defect.
