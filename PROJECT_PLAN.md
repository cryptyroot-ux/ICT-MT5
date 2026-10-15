# ICT Automation Project — Plan

> ## ⚠️ SUPERSEDED — historical planning record only
>
> This document is the **original upstream planning note**. Its CCXT / pandas /
> Flask / NautilusTrader architecture and its multi-market (XAUUSD, NAS100, US30)
> roadmap describe an approach that was **never implemented** and is not the current
> design.
>
> **The actual, authoritative plan and specification are:**
> - `docs/SMC_H1M5_SWEEPFVG_SPEC.md` — frozen strategy rules
> - `docs/superpowers/plans/2026-10-15-smc-h1m5-sweepfvg.md` — implementation plan
> - `docs/VALIDATION_REPORT.md` — evidence status and known blocker
>
> The shipped implementation is native **MQL5** (no CCXT, no Flask, no external
> services), targets **XAUUSD only**, and runs at **0.25 %-0.50 % risk per trade**
> (default 0.50 %), not 1 %. Do not plan new work from the content below.

## Context
User wants to learn ICT methodology and automate it. Deep research done. Architecture planned.

## ICT Core Concepts (Learned)
1. Market Structure — BoS (continuation), ChoCh (reversal)
2. Liquidity — BSL (swing highs), SSL (swing lows), liquidity sweeps
3. Order Blocks — last opposing candle before institutional displacement
4. Fair Value Gaps — 3-candle imbalances
5. Kill Zones — London Open (03:00-05:00 NY), NY Open (07:00-10:00 NY)
6. PD Array Matrix — Premium/Discount zones + 8 trigger tools
7. Optimal Trade Entry — Fibonacci 62-79% retracement
8. Daily Bias — top-down: Daily bias → 4H structure → 15M/5M/1M execution

## Existing Libraries
- `smartmoneyconcepts` (joshyattridge) — FVG, OB, BOS/CHOCH, Swing Highs/Lows
- `ICT-NT` (islero/NautilusTrader) — full rule-based engine, backtesting, position management
- Grokipedia rule-based approach — boolean detection functions, Backtrader integration

## 5-Layer Architecture
1. **Data Engine** — CCXT + pandas, multi-timeframe, session-aware
2. **Detection Engine** — Market Structure, Liquidity, OB, FVG, Kill Zone, PD Array
3. **Strategy Engine** — 6-condition logic tree for buy/sell signals
4. **Risk & Position Management** — 0.5-1% risk, TP1 1.5R / TP2 3R, trailing stop
5. **Execution & Backtesting** — Flask webhook / CCXT direct, Backtrader/NautilusTrader

## Execution Roadmap
- Phase 1 (wk 1-3): Detection layer — indicators, historical testing
- Phase 2 (wk 3-5): PD Array Engine — daily bias + Fibonacci zones + ranking
- Phase 3 (wk 5-8): Strategy logic + backtesting (6+ months, multiple markets)
- Phase 4 (wk 8-10): Execution layer (paper trade 4 weeks first)
- Phase 5 (wk 10+): Live trading with journal

## Open Questions
1. Target market: FX / indices / crypto?
2. Execution path: TradingView alerts or direct broker API?
3. Capital and risk per trade?

## Status
SUPERSEDED. The questions above were never answered and are moot: the shipped target is
XAUUSD on HFM Pro / HFM Demo, executed natively in MetaTrader 5 via MQL5. See
`docs/VALIDATION_REPORT.md` for current status.