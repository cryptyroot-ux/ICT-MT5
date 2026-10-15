# Changelog — ICT-MT5

## [SMC 1.0.0-research] — 2026-10-15

Fork of `MB-Ndhlovu/ICT-MT5` at `e1d1a80bee2454ef507f89c69256036a0a97dd5e`
(`cryptyroot-ux/ICT-MT5`, branch `feature/smc-h1m5-sweepfvg`).

Added the `SMC-H1M5-SweepFVG` strategy for XAUUSD (H1 context, M5 execution) as a
**separate** implementation. The upstream `ICT_*` EA and modules are preserved
unmodified so the original behavior stays auditable and available as the B0 reference.

### Added

- `Experts/SMC_H1M5_SweepFVG.mq5` — lifecycle, state orchestration, execution coordination.
  Hard-blocked on real accounts; rejects non-M5 chart timeframes; refuses to trade until a
  verified cost model is configured.
- `Include/SMC_Config.mqh` — frozen strategy constants, tick rounding helpers,
  deterministic setup-ID construction, fail-closed configuration validation.
- `Include/SMC_MarketStructure.mqh` — span-2 confirmed H1 pivots, close-only BOS,
  one break per structural level, bias reset on an opposing break.
- `Include/SMC_Liquidity.mqh` — M5 swing pools with equal-high/low tolerance, bounded
  sweep excursion + reclaim, stored extreme, next-opposing-liquidity target lookup.
- `Include/SMC_Displacement.mqh` — completed-bar SMA ATR(14), post-sweep internal pivot
  break, frozen displacement measures (body ≥ 1×ATR, body/range ≥ 0.60, outer quartile).
- `Include/SMC_FVG.mqh` — chronological three-candle gap with the displacement as C2;
  fresh / partially-mitigated / consumed / invalidated lifecycle.
- `Include/SMC_SetupStateMachine.mqh` — explicit WAIT_BIAS → … → COMPLETED sequence with
  INVALIDATED/EXPIRED terminal states, skip/reuse refusal, and restart position recovery.
- `Include/SMC_RiskManager.mqh` — persistent broker-day equity reference (with a
  same-day reset guard), volume flooring to broker step with below-minimum rejection,
  `OrderCalcProfit` cross-checked against tick properties, cost-inclusive net R:R.
- `Include/SMC_Execution.mqh` — pending-limit submission with broker-supported filling
  mode, spread/stops/freeze/target/risk revalidation, retcode plus terminal-order and
  position reconciliation, delayed-state blocking against duplicate submission, pending
  cancellation/modify, and a cost-gated emergency close.
- `Include/SMC_TradeLedger.mqh` — position-lifecycle accounting from actual deal history
  by position identifier and magic number, including partial closes, multiple exit deals,
  commission, swap, and fee; consecutive-loss reconstruction; fail-closed on reversal deals.
- `Include/SMC_Logger.mqh` — machine-readable CSV event log with a configuration hash.
- `Scripts/SMC_SymbolSpec.mq5` — measures the real broker symbol specification and
  cross-checks tick properties against `OrderCalcProfit`.
- `research/smc_core.py` — deterministic reference rules used by the test fixtures.
- `research/experiment.py` — B0–B3 runner gated on a hash-verified broker data manifest;
  refuses synthetic, third-party, and bar-model data for out-of-sample claims; refuses to
  synthesize B0; chronological splits with boundary purging; trade-derived metrics;
  block-bootstrap expectancy CI; frozen gate evaluation returning PASS/FAIL/INCONCLUSIVE.
- `tests/` — 54 deterministic tests (reference logic, evidence gates, MQL static structure).
- `docs/` — `SOURCE_AUDIT.md`, `SMC_H1M5_SWEEPFVG_SPEC.md`, `EXPERIMENT_PROTOCOL.md`,
  `BROKER_CONFIGURATION.md`, `INSTALLATION.md`, `VALIDATION_REPORT.md`, plus the
  design spec and implementation plan under `docs/superpowers/`.

### Audit findings on the upstream baseline

Documented in `docs/SOURCE_AUDIT.md` (14 findings: 4 Critical, 6 High, 3 Medium, 1 Low),
including invalid escaped-quote literals that prevent compilation, market-order entries
instead of limit entries, minimum-volume uplift that can exceed the risk budget,
synthetic ±1R/3R deal accounting, no restart reconciliation, mixed live-Bid /
historical-close sweep detection, unconfirmed-candle FVG scanning, forced FOK, and
performance artifacts built from synthetic or Yahoo data with look-ahead and
non-equivalent exits.

### Verification status at this commit

- Deterministic tests: 62 passed (executed).
- MQL5 compilation: **NOT VERIFIED** — no MetaEditor binary available on the authoring host.
- Broker specification: **NOT VERIFIED** — no HFM Pro account or symbol export available.
- B0–B3 quantitative results and all frozen acceptance gates: **INCONCLUSIVE**.
- Status: **RESEARCH / DEMO ONLY**. No profitability claim.

### Follow-up: exploratory bar-model analysis on the real HFM export

An HFM Demo4 **Cent/IDR** bar export (M5 591,777 rows, 2017-10-17 → 2026-10-15) was
available and was used for a clearly labeled `EXPLORATORY_BAR_MODEL` analysis that
`research/experiment.py` refuses to treat as an out-of-sample claim.

Headline finding: with the strategy implemented exactly as frozen, the **next opposing
liquidity target sits a median 0.06 R from the sweep-extreme ± 0.5 × ATR(14) stop**, and
only **0.10 %** of events reach raw R ≥ 3. Because net R:R ≥ 3 requires
`reward ≥ 3·risk + 4·cost`, every variant produces 0–2 out-of-sample trades and collapses
to **zero trades with any positive transaction cost**. B0–B3 are therefore all
INCONCLUSIVE, and the blocking issue is a **specification tension, not an implementation
defect**. Resolution requires an explicit, re-frozen specification decision.

Two harness defects were found by this analysis and fixed, each with a regression test:
(1) PnL was computed as a bare `price_delta × volume` instead of converting through the
broker tick value, which silently produced R multiples around 1e-6; (2) FVG validity was
evaluated against the entire future history instead of the order's lifetime window,
rejecting 1,609 of 1,627 otherwise valid gaps.

---

## [v1.02] — 2026-05-23
### Stress Test Optimization — ATR-Adaptive Stops

**Root Cause Identified:**
- XAUUSD H1 mean bar range: 2,849 pips
- Original SL=8 pips was 97% too tight
- Original TP=24 pips was 99% too tight
- Strategy hit breakeven but not profiting


- Stress test results on 2Y XAUUSD H1 data showed: 946 trades, 27.9% win rate, +137% return with ATR-adaptive stops

**Code Changes:**

`ICT_RiskManager.mqh`:
- Added `ComputeATRStops()` — calculates SL = 0.5x ATR(H1) for XAUUSD, fixed stops for NAS100/US30
- Added `GetStopLossPips()`, `GetTakeProfitPips()`, `GetATRValue()` accessors
- Added `CalcLotSizeATR()`, `CalcATRStopLoss()`, `CalcARRTakeProfit()` helpers
- ATR recomputed every `Refresh()` tick (adapts to market volatility)
- ATR fallback: if indicator unavailable, uses fixed values (XAUUSD: 500p, NAS100: 15p, US30: 20p)

`ICT_KillZones.mqh`:
- Added XAUUSD-specific session weights: London(0.8), NY_OPEN(1.3), LONDON_CLOSE(0.6), NY_CLOSE(1.5)
- Added `IsGoldHighConvictionSession()` — only fires during weight ≥ 1.3 sessions
- Added `GetWeightedSessionProbability()` — baseProb × xauusdWeight for gold
- Added `SetSymbol()` to enable gold mode at init
- Enhanced pip tracking per session for win rate analysis

`ICT_SignalGenerator.mqh`:
- Updated `BuyRule3()`/`SellRule3()` to use gold high-conviction filter
- Updated `BoostScore()` to use weighted kill zone probability (0-9 points for gold)
- Added `ATRStopBuffer()` — replaces fixed pip buffer with 0.5x ATR minimum
- Signal output now includes ATR pips in reason string
- Stops and TPs now use `CalcATRStopLoss()` and `CalcARRTakeProfit()` from risk manager

`ICT_EA.mq5`:
- Version bumped to 1.02
- Calls `g_kz.SetSymbol(ICT_Symbol)` in OnInit to activate gold mode
- Debug output includes ATR values and weighted kill zone probability

**Key Metrics (v1.02 ATR-adaptive backtest on XAUUSD H1, 2Y):**
- Win rate: 27.9% (above 25% breakeven for 1:3 R:R)
- Expectancy: +$0.72/trade
- Starting capital: R500 → R1,186 (+137%)
- Best month: Jul 2025 +3,550%
- Worst month: May 2024 -1,000%

---

## [v1.01] — 2026-05-18
### Initial Production Build

Full MQL5 EA with all modules complete:
- MarketStructure, OrderBlocks, FairValueGap, LiquidityPools
- KillZones, PDArray, RiskManager, SignalGenerator
- Main EA shell with trade execution, daily loss cap, session tracking