# SMC-H1M5-SweepFVG Implementation Plan

> **For agentic workers:** This plan is being executed inline in this session. Each task is verified before moving to the next; no test report is upgraded beyond its evidence.

**Goal:** Add an auditable, deterministic, risk-controlled MT5 EA for the frozen SMC-H1M5-SweepFVG strategy and reproducible logic/research validation.

**Architecture:** Keep the upstream ICT EA/modules intact as legacy baseline. Add a separate SMC EA with signal, state, risk, execution, ledger, and logging modules. Use a pure-Python reference model for deterministic fixtures and data-gated B0–B3 experiments; MetaEditor/Strategy Tester results remain separate evidence.

**Tech Stack:** MQL5 native terminal/trade/history APIs; Python 3 standard library `unittest` for deterministic fixtures; CSV/JSON for evidence.

## Global Constraints

- Strategy name `SMC-H1M5-SweepFVG`; XAUUSD; H1 context; M5 execution.
- Risk default 0.50%, supported 0.25%–0.50%; daily loss cap 2%; two consecutive losing lifecycles; entry spread maximum 0.50.
- Entry is midpoint pending limit; stop is sweep extreme ±0.5 × completed M5 ATR(14); target is next opposing liquidity; net R:R ≥3; expiry is six M5 bars or 30 minutes.
- Acceptance gates are net expectancy ≥+0.25R, PF ≥1.50, max DD <12%, ≥100 completed trades, all risk limits correct, no unresolved critical execution/accounting defects. Never change gates after seeing results.
- Never use fabricated, synthetic, Yahoo futures, or HFM Demo4 evidence as HFM Pro OOS proof; never claim compile/runtime/performance success without that specific execution evidence.

## Files and contracts

- Audit/spec/protocol: `docs/SOURCE_AUDIT.md`, `docs/SMC_H1M5_SWEEPFVG_SPEC.md`, `docs/EXPERIMENT_PROTOCOL.md`.
- MQL modules: `Experts/SMC_H1M5_SweepFVG.mq5`, `Include/SMC_Config.mqh`, `SMC_MarketStructure.mqh`, `SMC_Liquidity.mqh`, `SMC_Displacement.mqh`, `SMC_FVG.mqh`, `SMC_SetupStateMachine.mqh`, `SMC_RiskManager.mqh`, `SMC_Execution.mqh`, `SMC_TradeLedger.mqh`, `SMC_Logger.mqh`.
- Reference model/tests: `research/smc_core.py`, `research/experiment.py`, `tests/test_smc_core.py`, `tests/test_experiment.py`.
- Operator evidence: `docs/BROKER_CONFIGURATION.md`, `docs/VALIDATION_REPORT.md`, `docs/INSTALLATION.md`, `CHANGELOG.md`, updated `README.md`.

## Task 1 — Record source audit and frozen mechanics

**Status:** Complete before implementation.

- [x] Read every tracked file and record finding ID, severity, location, behavior, impact, correction, test, and verification in `docs/SOURCE_AUDIT.md`.
- [x] Freeze explicit pivots, close-only BOS, reclaim timing, displacement measures, FVG life, order reference/expiry, risk, and experiment gates in documentation before OOS testing.
- [x] Verify fork branch points at upstream SHA; retain original implementation as historical B0 reference.

## Task 2 — Reference-model tests (TDD)

**Files:** Create `tests/test_smc_core.py`; then `research/smc_core.py`.

- [ ] First encode literal hand-checked fixtures for pivot confirmation, close-only BOS, sweep/reclaim sequencing and extreme, displacement/MSS ordering, bullish/bearish C1/C2/C3 gaps, midpoint/expiry, volume flooring, target R:R, state transitions, and position-lifecycle PnL.
- [ ] Run `python3 -m unittest -v tests.test_smc_core`; observe expected import/behavior failures before implementation.
- [ ] Implement the minimal reference functions required by those fixtures; rerun until all pass.
- [ ] Add boundary cases: incomplete candle, no reclaim, stale/duplicate pool, pre-sweep break/FVG, one-tick gap, wrong tick size, below-min volume, unknown monetary properties, invalid state transition, daily cap, two losses, partial exits, restart reconciliation.

## Task 3 — Signal/state MQL5 modules

**Files:** Create config, structure, liquidity, displacement, FVG, and state-machine includes plus behavior fixtures in `tests/`.

- [ ] Port only the frozen mechanics using completed `MqlRates` arrays and symbol-specific tick size; keep setup IDs and timestamps through each module boundary.
- [ ] Add state transition event/reason recording and reject skip/reuse/terminal-state transitions.
- [ ] Add source/reference consistency checks that execute reference fixtures; separately mark them as non-MQL execution tests.

## Task 4 — Risk, ledger, execution, and observability

**Files:** Create `SMC_RiskManager.mqh`, `SMC_TradeLedger.mqh`, `SMC_Execution.mqh`, `SMC_Logger.mqh`, and EA lifecycle file.

- [ ] Size volume from actual monetary loss (`OrderCalcProfit` cross-checked with tick values), floor to broker step, reject below minimum, and recompute after normalization.
- [ ] Use stable broker-day reference and magic-scoped actual deal/position lifecycle accounting; fail closed if reconciliation fails.
- [ ] Submit midpoint pending limits only after spread, symbol, costs, risk, target, stop/freeze, filling, ownership, and setup revalidation; inspect retcodes and reconcile actual order/deal state.
- [ ] Persist structured CSV events, setup/order/position IDs, actual costs, and lifecycle reasons; bound in-memory scans/history use.

## Task 5 — B0–B3 runner and reports

**Files:** Create `research/experiment.py`, `tests/test_experiment.py`, `docs/EXPERIMENT_PROTOCOL.md`, `docs/VALIDATION_REPORT.md`.

- [ ] Require a hash-checked broker data manifest with symbol, timezone, dates, data/modeling type, spread, commission, swap, slippage, and account currency; refuse synthetic or mismatched OOS claims.
- [ ] Implement chronological train/validation/OOS boundaries and purging of crossing setups/orders/positions; freeze config before OOS.
- [ ] Report actual simulated lifecycle outcomes and all required metrics/CIs/stress configurations; if data or sample count is insufficient, report INCONCLUSIVE.

## Task 6 — Documentation and verification

**Files:** `README.md`, `CHANGELOG.md`, `docs/BROKER_CONFIGURATION.md`, `docs/INSTALLATION.md`, `docs/VALIDATION_REPORT.md`.

- [ ] Document HFM Pro properties as unverified unless obtained from that actual symbol/account; explain demo-only readiness and no-live-order policy.
- [ ] Run Python syntax checks and all deterministic tests. Locate/invoke official MetaEditor and Strategy Tester only if available; otherwise retain NOT VERIFIED.
- [ ] Review final diff and clean status; commit the actual changes to `feature/smc-h1m5-sweepfvg`, push, verify remote SHA, and create a fork-local PR if repository workflow permits.
