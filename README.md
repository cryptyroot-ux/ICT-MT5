# ICT-MT5 — Automated ICT Smart Money Concepts EA

> **Upstream author:** Malibongwe Ndhlovu — <https://github.com/MB-Ndhlovu/ICT-MT5>
> **Fork:** cryptyroot-ux/ICT-MT5
> **Execution:** MetaTrader 5 / MQL5
> **Upstream target markets:** XAUUSD, NAS100, US30

---

## ⚠️ Read this first: two different codebases live in this repository

| | Upstream baseline `ICT_*` | Target strategy `SMC_*` |
|---|---|---|
| Entry point | `Experts/ICT_EA.mq5` | `Experts/SMC_H1M5_SweepFVG.mq5` |
| Status | **Preserved unmodified for audit.** Does not compile as received (`docs/SOURCE_AUDIT.md` AUD-001) | New implementation of the frozen strategy |
| Logic | D1 premium/discount bias, kill zones, order blocks, confidence scoring, market orders, ATR-from-entry stops | H1 confirmed BOS → M5 liquidity sweep/reclaim → post-sweep displacement + CHoCH → displacement-created 3-candle FVG → midpoint limit entry, sweep-extreme stop |
| Evidence | The checked-in CSVs/GIF are synthetic or Yahoo-derived and are **not** performance evidence | Compile status: **NOT VERIFIED**; OOS performance: **NOT VERIFIED** |

**Do not attach `ICT_EA`.** Use `SMC_H1M5_SweepFVG`, which is hard-blocked on real
accounts and refuses to trade until a verified broker cost model is supplied.

## SMC-H1M5-SweepFVG (target strategy)

**Strategy:** liquidity-sweep reversal with post-sweep displacement, CHoCH/MSS, a
displacement-created fair value gap, and a retracement limit entry.
**Instrument:** XAUUSD · **Context:** H1 · **Execution:** M5

Frozen rules (full detail in `docs/SMC_H1M5_SWEEPFVG_SPEC.md`):

1. **H1 bias** — close-only BOS through a span-2 pivot confirmed two completed candles
   earlier. No D1 midpoint bias, no premium/discount score.
2. **M5 sweep** — a completed M5 candle trades ≥ 2 symbol ticks through a confirmed
   sell-side (for BUY) or buy-side (for SELL) pivot; a completed candle closes back
   beyond the level within 3 candles. The sweep extreme is recorded for the stop.
3. **Post-sweep displacement + CHoCH** — the candle must close through an internal pivot
   formed *after* reclaim, with body ≥ 1.0 × completed-bar M5 ATR(14), body/range ≥ 0.60,
   and close in the outer range quartile.
4. **FVG** — C2 is the displacement candle; bullish `Low(C3) > High(C1)`, bearish
   `High(C3) < Low(C1)`; gap ≥ 1 symbol tick; bound to this displacement and setup ID.
5. **Entry** — pending **limit** order at the FVG midpoint (50%). Expires at the earlier
   of 6 completed M5 candles or 30 minutes after the C3 confirmation.
6. **Stop** — sweep extreme ∓ 0.5 × completed M5 ATR(14), rounded outward to tick size.
7. **Target** — the next opposing confirmed liquidity level; **net R:R ≥ 3.00** after
   spread, commission, fees, and slippage. Otherwise no trade.
8. **Risk** — 0.25 %–0.50 % per trade (default 0.50 %) of a stable broker-day equity
   reference; 2 % daily cap; entries stop after two consecutive losing position
   lifecycles; max entry spread 0.50 price units.
9. **Accounting** — realized PnL, daily loss, and loss streak come from actual deal
   history by position identifier and magic number, including commission, swap, and fee.

### Source layout

```text
Experts/SMC_H1M5_SweepFVG.mq5   EA lifecycle, state orchestration, execution coordination
Include/SMC_Config.mqh           frozen constants, tick helpers, config validation
Include/SMC_MarketStructure.mqh  confirmed H1 swings, close-only BOS, bias
Include/SMC_Liquidity.mqh        M5 pivots, sweep excursion/reclaim, target pools
Include/SMC_Displacement.mqh     completed-bar M5 ATR, post-sweep displacement + CHoCH
Include/SMC_FVG.mqh              three-candle gap detection and validity lifecycle
Include/SMC_SetupStateMachine.mqh explicit state transitions and terminal states
Include/SMC_RiskManager.mqh      monetary risk, volume flooring, daily cap
Include/SMC_Execution.mqh        pending-order lifecycle, retcodes, emergency close
Include/SMC_TradeLedger.mqh      position-lifecycle PnL, costs, restart recovery
Include/SMC_Logger.mqh           machine-readable CSV event log + config hash
Scripts/SMC_SymbolSpec.mq5       measure the real broker symbol specification
research/                        deterministic reference model + B0-B3 runner
tests/                           deterministic logic and static-structure tests
```

### Run the tests

```bash
python3 -m unittest discover -s tests -v
```

These are deterministic logic and static-structure tests. They are **not** MQL5 compile
tests and **not** MT5 runtime tests.

### Current evidence status

Compilation **NOT VERIFIED** (no MetaEditor on the authoring host). Broker
specification **NOT VERIFIED**. B0–B3 out-of-sample results **NOT VERIFIED / INCONCLUSIVE**.
All frozen acceptance gates remain **INCONCLUSIVE**. See `docs/VALIDATION_REPORT.md`.

**This project is RESEARCH / DEMO ONLY. No profitability claim is made.**

---

## Upstream baseline (unchanged)

> **Author:** Malibongwe Ndhlovu
> **Supervisor:** Malibongwe Ndhlovu
> **Execution:** MetaTrader 5 / MQL5
> **Target Markets:** XAUUSD, NAS100, US30
> **Risk Model:** 1% per trade, 2% daily loss cap

## Overview

ICT-MT5 is a modular Expert Advisor that converts ICT / Smart Money Concepts into a rule-based MQL5 trading engine for MetaTrader 5. The project is built to be readable, testable, . Just structure, rules, and risk control.

The EA combines:
- market structure detection
- order block detection
- fair value gap detection
- liquidity pool and sweep tracking
- kill zone timing filters
- premium / discount analysis
- fixed risk management
- a single signal aggregator

## Repository Layout

```text
ICT-MT5/
├── Experts/
│   └── ICT_EA.mq5
├── Include/
│   ├── ICT_MarketStructure.mqh
│   ├── ICT_OrderBlocks.mqh
│   ├── ICT_FairValueGap.mqh
│   ├── ICT_LiquidityPools.mqh
│   ├── ICT_KillZones.mqh
│   ├── ICT_PDArray.mqh
│   ├── ICT_RiskManager.mqh
│   └── ICT_SignalGenerator.mqh
├── docs/
│   ├── BACKTEST_GUIDE.md
│   └── ICT_METHODOLOGY.md
└── README.md
```

## Module Summary

| Module | Purpose | Key Dependencies |
|---|---|---|
| `ICT_MarketStructure.mqh` | Detects swing highs/lows, Break of Structure, Change of Character, and higher-timeframe alignment. | Native price series functions |
| `ICT_OrderBlocks.mqh` | Identifies bullish and bearish order blocks and tracks active, weakening, and broken zones. | `ICT_MarketStructure.mqh` |
| `ICT_FairValueGap.mqh` | Detects 3-candle fair value gaps and tracks fill, mitigation, and widening state. | Native price series functions |
| `ICT_LiquidityPools.mqh` | Tracks buy-side liquidity, sell-side liquidity, equal highs, equal lows, and sweep confirmations. | Native price series functions |
| `ICT_KillZones.mqh` | Manages London and New York kill zones, session state, and one-trade-per-session behaviour. | Native time functions |
| `ICT_PDArray.mqh` | Builds premium / discount zones, OTE windows, Fibonacci levels, and daily bias. | `ICT_MarketStructure.mqh` |
| `ICT_RiskManager.mqh` | Handles pip sizing, lot sizing, daily loss caps, drawdown state, and trade history. | Native account and symbol info |
| `ICT_SignalGenerator.mqh` | Combines all modules into a final buy / sell / none signal with confidence scoring. | All modules above |
| `ICT_EA.mq5` | Main EA shell that initialises modules, filters trades, and sends orders. | All modules above |

## Installation

1. Clone the repository.
   ```bash
   git clone https://github.com/MB-Ndhlovu/ICT-MT5.git
   ```
2. Copy the files into MetaTrader 5:
   - `.mqh` files into `MQL5/Include/`
   - `ICT_EA.mq5` into `MQL5/Experts/`
3. Restart MetaTrader 5.
4. Open `ICT_EA` from Navigator and attach it to the chart.
5. Enable Algo Trading and confirm the input settings.

## Recommended Usage

- **XAUUSD:** M5, higher noise, wider stops
- **NAS100:** H1, cleaner structure, lower frequency
- **US30:** H1, similar behaviour to NAS100

Run the EA on a VPS or a stable terminal if you want consistent session coverage. Trading from a laptop like it is 2009 is a choice, not a strategy.

## Input Parameters

| Input | Default | Description |
|---|---:|---|
| `ICT_Symbol` | `XAUUSD` | Trading symbol used by the EA. |
| `ICT_ExecTf` | `PERIOD_M5` | Execution timeframe. |
| `ICT_RiskPercent` | `1.0` | Risk per trade as a percentage of equity. |
| `ICT_MaxDailyLoss` | `2.0` | Daily loss cap in percent. |
| `ICT_MaxTrades` | `3` | Maximum number of trades per day. |
| `ICT_Lookback` | `5` | Swing / liquidity lookback for structure detection. |
| `ICT_UseLondonKZ` | `true` | Enables London kill zone logic. |
| `ICT_UseNYKZ` | `true` | Enables New York kill zone logic. |
| `ICT_UseHigherTFFilter` | `true` | Requires higher-timeframe structure alignment. |
| `ICT_MinConfidence` | `60` | Minimum signal confidence needed before entry. |
| `ICT_UseKillZoneOnly` | `true` | Hard session filter for entries. |
| `ICT_FixedLot` | `0.0` | Optional fixed lot override for testing only. |
| `ICT_DebugMode` | `true` | Prints diagnostic status to the terminal. |

## Backtesting Workflow

1. Open **MetaTrader 5 → Strategy Tester**.
2. Select `ICT_EA` as the expert.
3. Use a high-quality history source and tick data where possible.
4. Test the exact symbol / timeframe pair you intend to trade.
5. Review trade logs, session behaviour, and drawdown behaviour.

Detailed backtest guidance lives in `docs/BACKTEST_GUIDE.md`.

## Roadmap

- [x] Modular ICT detection engine
- [x] Risk-controlled EA shell
- [x] Session and liquidity logic
- [x] Portfolio-ready documentation
- [x] Backtest guide and methodology notes
- [ ] Visual chart annotations for structure, OBs, FVGs, and liquidity
- [ ] Multi-symbol optimisation presets
- [ ] Walk-forward testing report template
- [ ] Live trading journal export
- [ ] Performance dashboard for results review

## Disclaimer

This project is for educational and portfolio purposes only. It is not financial advice. Backtest thoroughly before any live deployment.
