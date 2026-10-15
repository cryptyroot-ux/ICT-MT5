# Bantu-OS — ICT Automation Project

> ## ⚠️ PARTIALLY SUPERSEDED — read before editing
>
> The `ICT_*` EA and modules below are the **legacy upstream baseline**, preserved
> unmodified for audit and as the B0 reference. They do **not** compile as received
> (`docs/SOURCE_AUDIT.md`, AUD-001) and must not be attached.
>
> The **active** strategy is `SMC-H1M5-SweepFVG`. Before changing anything, read
> `docs/SMC_H1M5_SWEEPFVG_SPEC.md` (frozen rules) and `docs/VALIDATION_REPORT.md`
> (evidence status).
>
> **Superseded statements in this file, which must not be followed:**
> - "1% per trade" and "1% risk per trade, 0.5% optional tightening" — **replaced** by
>   the frozen range 0.25 %–0.50 % per trade, default **0.50 %**.
> - NAS100 / US30 as trading targets — **out of scope**; the target is XAUUSD only.
> - Kill zones, PD Array premium/discount, and D1 bias as entry logic — **not used**
>   by the SMC strategy (see the frozen sequence in the spec).
>
> Legacy baseline retained below for historical reference only.


## Project: ICT Trading EA for MT5 (MQL5)

**Supervisor:** Malibongwe Ndhlovu
**Markets:** XAUUSD, NAS100, US30
**Risk (legacy baseline, superseded):** 1% per trade | Daily loss cap: -2%
**Risk (active SMC strategy):** 0.25 %-0.50 % per trade, default 0.50 % | Daily loss cap 2 %
**Execution:** MQL5 EA via MetaTrader 5

## Architecture

```text
Experts/ICT_EA.mq5                  ← Main EA shell
Include/ICT_MarketStructure.mqh     ← Structure engine
Include/ICT_OrderBlocks.mqh         ← Order block engine
Include/ICT_FairValueGap.mqh        ← FVG engine
Include/ICT_LiquidityPools.mqh      ← Liquidity engine
Include/ICT_KillZones.mqh           ← Session timing engine
Include/ICT_PDArray.mqh             ← Premium / discount engine
Include/ICT_RiskManager.mqh         ← Risk and sizing engine
Include/ICT_SignalGenerator.mqh     ← Signal aggregation engine
```

## Current Status

- [x] ICT_MarketStructure — complete
- [x] ICT_OrderBlocks — complete
- [x] ICT_FairValueGap — complete
- [x] ICT_LiquidityPools — complete
- [x] ICT_KillZones — complete
- [x] ICT_PDArray — complete
- [x] ICT_RiskManager — complete
- [x] ICT_SignalGenerator — complete
- [x] ICT_EA (main shell) — complete
- [x] README.md — updated
- [x] BACKTEST_GUIDE.md — updated
- [x] ICT_METHODOLOGY.md — updated
- [x] CHANGELOG.md — updated

## Market-Specific Settings

| Market | Timeframe | Lookback | Notes |
|---|---|---:|---|
| XAUUSD | M5 | 3–5 | High noise, wider stops |
| NAS100 | H1 | 8–12 | Slower structure, avoid M1 |
| US30 | H1 | 8–12 | Correlates with DXY + SPX |

## Supervisor Preferences

- Report format: supervisor style — design decisions, flags, testing checklist
- Build directly and explain simultaneously
- No fluff. Direct, technical, honest.
- Legacy baseline used 1% per trade — superseded; SMC uses 0.25 %-0.50 %, default 0.50 %
- Starting capital is irrelevant; use an R-based system

## Notes

Keep this file compact and current. It is a routing map, not a transcript log.
## Active SMC Architecture (authoritative for this branch)

| File | Responsibility |
|---|---|
| `Experts/SMC_H1M5_SweepFVG.mq5` | EA lifecycle, state orchestration, execution coordination |
| `Include/SMC_Config.mqh` | Frozen constants, tick helpers, fail-closed config validation |
| `Include/SMC_MarketStructure.mqh` | Confirmed H1 pivots, close-only BOS, bias |
| `Include/SMC_Liquidity.mqh` | M5 pivots, sweep excursion/reclaim, target pools |
| `Include/SMC_Displacement.mqh` | Completed-bar M5 ATR, post-sweep displacement + CHoCH |
| `Include/SMC_FVG.mqh` | Three-candle gap detection and validity lifecycle |
| `Include/SMC_SetupStateMachine.mqh` | Explicit state transitions, terminal states |
| `Include/SMC_RiskManager.mqh` | Monetary risk, volume flooring, daily cap |
| `Include/SMC_Execution.mqh` | Pending-order lifecycle, retcodes, emergency close |
| `Include/SMC_TradeLedger.mqh` | Position-lifecycle PnL, costs, restart recovery |
| `Include/SMC_Logger.mqh` | Machine-readable CSV event log + config hash |

## Non-negotiable rules for the SMC strategy

1. The frozen acceptance thresholds (+0.25R expectancy, 1.50 PF, <12 % drawdown, ≥100
   completed trades) **must not be relaxed, reinterpreted, or averaged** after results
   are viewed. A sample-size shortfall is INCONCLUSIVE, never a pass.
2. Risk per trade is 0.25 %-0.50 % of a stable broker-day equity reference (default
   0.50 %). The daily cap is 2 %. Entries stop after two consecutive losing position
   lifecycles.
3. Volume is floored to the broker step; below-minimum is **rejected**, never uplifted.
4. Realized PnL, daily loss, and loss streak come from actual deal history by position
   identifier and magic number. Never synthesize "+3R"/"-1R".
5. Real accounts are hard-blocked at init. Trading is blocked until a verified
   transaction-cost model is configured.
6. Never hardcode a pip, contract size, tick value, or volume step. Read them from the
   symbol and fail closed.
7. Only closed candles may confirm structure, sweeps, displacement, FVGs, or BOS.

## Known blocker

The frozen stop rule (sweep extreme ± 0.5 × M5 ATR(14)) and the frozen target rule
(next opposing liquidity) are near-mutually-unsatisfiable on the available data: median
raw R of 0.06 and only 0.10 % of events reaching 3R. Resolving it requires an explicit,
re-frozen specification decision — not a threshold change. See
`docs/VALIDATION_REPORT.md` §4.2.
