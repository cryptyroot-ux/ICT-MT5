# Contributing to ICT-MT5

> **Scope note.** `Experts/ICT_EA.mq5` and `Include/ICT_*.mqh` are the legacy upstream
> baseline, preserved unmodified and **not compiling as received**. Contributions
> should target `SMC-H1M5-SweepFVG` and its `Include/SMC_*.mqh` modules. Read
> `docs/SMC_H1M5_SWEEPFVG_SPEC.md` first; the rules below are split into legacy and
> active strategy where they differ.

Thanks for helping improve ICT-MT5. External contributions are welcome, especially fixes, documentation upgrades, backtest notes, and module refinements.

All contributions must be submitted as pull requests to the `main` branch.

## Pull Request Process

1. Fork the repository and create a feature branch from `main`
2. Ensure all modules compile without errors in MetaEditor
3. Add new modules with full header documentation (author, supervisor, date, purpose)
4. Update README.md to reflect any new files or architectural changes
5. Run a backtest with your changes before submitting the PR
6. All PRs require a description explaining the change and its rationale
7. If your change improves ICT logic, include a short note on why it is better than the previous version

## Code Standards

- All `.mqh` files must include header with: Author, Supervisor, Date, Purpose
- All enums and structs must be documented
- Debug prints must be gated behind `ICT_DebugMode`
- No hardcoded magic numbers — use named constants or inputs
- Legacy baseline used a 1% cap. **Active strategy:** risk is 0.25 %-0.50 % per
  trade (default 0.50 %) of a stable broker-day equity reference; the 2 % daily
  cap and the two-consecutive-loss rule are hard limits, not tunable.
- No indicator anti-patterns: no repainting, no future-leaking buffers

## Architecture Rules

Legacy baseline (kept for audit, not for extension):

- `ICT_MarketStructure.mqh` is the base dependency — all others may reference it
- `ICT_SignalGenerator.mqh` is the aggregator — references all other modules
- `ICT_EA.mq5` is the only file that references everything — it is the entry point
- Kill Zones are a hard filter in the legacy EA — **not** a filter in the SMC strategy

Active strategy (`SMC_*`):

- `Experts/SMC_H1M5_SweepFVG.mq5` is the single entry point and owns orchestration
- Signal generation, state, risk, execution, accounting, and logging are separate
  modules with one direction of dependency; no circular includes
- No module may fabricate PnL, wins/losses, or risk figures — all monetary results come
  from `OrderCalcProfit` and actual deal history
- Fail closed: missing symbol properties, unverified costs, or unreconstructable risk
  state must block entries rather than degrade gracefully

## Research integrity rules (non-negotiable)

- The frozen acceptance thresholds must never be relaxed, reinterpreted, or averaged
  after results are viewed. A sample-size shortfall is INCONCLUSIVE, never a pass.
- Out-of-sample data must never influence parameter selection.
- Synthetic, third-party, and bar-model data must be labeled as such and must not be
  presented as broker-grade out-of-sample evidence.
- Any result computed with a different model than the specification must be labeled a
  different strategy, not reported as evidence for the specified one.

If you're contributing from outside the core project, keep the changes small and focused. Clean pull requests beat heroic spaghetti every time.