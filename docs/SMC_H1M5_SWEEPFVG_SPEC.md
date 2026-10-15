# SMC-H1M5-SweepFVG — Frozen Strategy Specification

**Version:** 1.0.0-research  
**Strategy:** `SMC-H1M5-SweepFVG`  
**Instrument:** broker symbol configured as XAUUSD (default exact name `XAUUSD`)  
**Context:** H1  
**Execution:** M5  
**Status:** Research/demo implementation; profitability and broker execution are not established.

This file makes the user-supplied frozen rules executable and records conservative choices where the prompt required a precise definition. These values are frozen for the initial B0–B3 experiment; they are not tuning ranges.

## 1. Completed-candle structure and H1 bias

- A swing high/low is a strict local extreme with two completed candles on each side (fractal span 2). Equal neighboring highs/lows do not create a strict pivot.
- The pivot becomes known only at the close of its second right-hand candle. Every scan uses completed rates; the live M5/H1 candle is never a confirmation candle.
- A bullish BOS is the close of a completed H1 candle strictly above the latest confirmed swing high. Bearish BOS is the symmetric close below the latest confirmed swing low. A wick-only break is not a BOS. No extra point buffer is applied.
- A BOS event is keyed by direction, swing-level pivot time, and confirming candle time and cannot be counted twice. The latest BOS sets bias. An opposing BOS replaces it; missing history or conflicting/invalid structure yields neutral bias and no entries.
- The target strategy does not use D1 midpoint bias, a premium/discount score, order blocks, or kill zones.

## 2. Liquidity, sweep, reclaim, and setup identity

- SSL and BSL levels are confirmed M5 swing lows and highs. Pivots separated by no more than two actual symbol tick sizes are treated as one equal-low/equal-high pool; the most recently confirmed pivot price/time anchors the pool.
- Liquidity is fresh for at most 100 completed M5 candles after pivot confirmation. Older levels are stale. When two distinct eligible levels are swept and reclaimed on the same candle, the event is ambiguous and rejected.
- A sweep excursion requires a completed M5 candle to trade at least two tick sizes through a pool. Reclaim confirmation requires a completed candle to close back beyond the pool in the reversal direction, within three completed candles of the excursion. Same-candle excursion and reclaim is permitted. The sweep extreme is the minimum low (BUY) or maximum high (SELL) observed from excursion through reclaim.
- A BUY consumes SSL; a SELL consumes BSL. A setup ID is deterministically derived from symbol, side, anchor level/pivot time, and sweep excursion time. A swept pool/setup cannot be reused.
- Sweep and reclaim use the same completed M5 price series, not a live quote combined with a historical close.
- A developing setup expires if no post-sweep displacement/CHoCH is confirmed within 12 completed M5 candles (one hour). Its C3 must be the next completed candle after displacement C2; if it does not form the qualifying FVG, that setup is invalidated rather than waiting for an unrelated later gap.

## 3. Post-sweep displacement and CHoCH/MSS

- The CHoCH/MSS candle must close after the sweep-reclaim confirmation.
- Internal structure is the latest confirmed opposite-side M5 fractal pivot formed after reclaim and confirmed before the break candle. BUY requires close strictly above that confirmed internal swing high; SELL requires close strictly below the confirmed internal swing low.
- The break candle is a qualifying displacement candle only when (a) body length is at least 1.0 × completed-bar M5 ATR(14), (b) body/range is at least 0.60, and (c) close is in the top 25% of range for BUY or bottom 25% for SELL. A zero-range candle fails.
- ATR(14) is the simple moving average of the last 14 true ranges, based on completed M5 bars only. The displacement event is unique by pivot time and confirmation time.

## 4. Displacement-created FVG and validity

- The displacement/CHoCH candle is the middle (C2) candle of the three-candle pattern. C1 is two bars before C2; C3 is the first completed candle after C2. Therefore FVG confirmation occurs at C3 close, after the displacement event.
- Bullish: `Low(C3) > High(C1)`, lower=`High(C1)`, upper=`Low(C3)`. Bearish: `High(C3) < Low(C1)`, lower=`High(C3)`, upper=`Low(C1)`.
- Gap width must be at least one actual symbol tick. C1/C2/C3 must all be completed and ordered after the sweep as required. The gap is bound to exactly one qualifying displacement and setup ID; an unrelated or older FVG cannot qualify.
- **Fresh:** no later completed M5 candle has traded into the zone. **Partially mitigated:** price has entered the zone but has not traversed its far edge; this alone does not invalidate a still-unfilled midpoint order. **Consumed:** a later completed candle has traversed the entire zone. **Invalidated:** a completed candle closes beyond the far boundary (below the lower edge for bullish, above the upper edge for bearish). Invalidated/consumed FVGs cannot create/recreate an order.

## 5. Entry, order expiry, and setup invalidation

- Primary model: FVG midpoint (50%). Edge entry is not active in this version.
- Place a BUY LIMIT at the midpoint only while the selected price is below current Ask; place a SELL LIMIT only while above current Bid. If already crossed/invalid, reject; never substitute a market order.
- The order reference event is the completed C3 candle close that confirms the FVG. Expiry is the earlier of six completed M5 candles after that event or 30 minutes after it (both are 30 minutes on M5). The request uses broker server time and is also reconciled by candle count.
- Before submit and while pending, revalidate H1 bias, setup state, sweep/CHoCH/FVG identity and validity, target, spread, risk budget, EA-owned positions/orders, and broker constraints. Opposing H1 BOS, a completed close beyond the sweep extreme, an invalidated/consumed FVG, a target change that fails net R:R, or expiry cancels the order. A terminal setup is never recreated.
- No valid next-liquidity target means no trade.

## 6. Stop, liquidity target, and transaction-cost gate

- BUY stop: `sweep low - 0.5 × completed M5 ATR(14)`. SELL stop: `sweep high + 0.5 × completed M5 ATR(14)`.
- SL and target are rounded outward only to actual symbol tick increments (BUY SL down, SELL SL up; BUY target down, SELL target up). The resulting exact price and monetary risk are recalculated; any broker-required wider adjustment is rejected.
- BUY TP is the nearest still-valid confirmed BSL above entry; SELL TP is the nearest still-valid confirmed SSL below entry. Arbitrary-distance targets are forbidden.
- Expected reward and loss are calculated in account currency using actual pending entry, stop/target prices, volume, and `OrderCalcProfit`, cross-checked against broker tick properties. Net R:R must be at least 3.00 after a conservative current-spread charge, configured round-turn commission/fees estimate, and separately configured slippage estimate.
- If costs are not verified/configured or the target cannot produce net R:R ≥3.00, reject. The EA does not silently move a target or widen a stop.

## 7. Risk and ownership

- Risk setting is 0.25%–0.50% of the stable broker-day equity reference; default 0.50%. Values outside the interval fail configuration validation.
- Daily entry-loss limit: 2% of the stable broker-day equity reference. Once reached, cancel EA-owned pending entry orders and block new orders; do not close an existing position solely due to the daily cap.
- Stop new entries after two consecutive completed losing EA-owned position lifecycles; resume next broker server day. Results are total actual PnL for a position lifecycle, including profit, commission, swap, and fee. Zero/non-losing lifecycle resets the streak.
- Risk volume is rounded down to `SYMBOL_VOLUME_STEP`; if below minimum, reject. No minimum-volume uplift. Final normalized risk must be within budget.
- Dedicated magic number and stable order/deal/position identifiers isolate this EA. Startup reconciliation must complete before entries are enabled; unknown/unreconstructable state fails closed.
- Maximum entry spread: 0.50 XAUUSD price units (Ask−Bid at submission).

## 8. Required state sequence

`WAIT_BIAS → WAIT_SWEEP → WAIT_DISPLACEMENT_CHOCH → WAIT_FVG → WAIT_RETRACE_OR_ORDER → ORDER_PENDING → IN_POSITION → COMPLETED`

Every terminal path records a timestamp, setup ID, prior/next state, levels, and reason. `INVALIDATED` and `EXPIRED` are terminal. A setup, order, and position have separate identifiers and lifecycles.

Event timestamps are broker-server bar-open timestamps for the candle that confirmed the event; an order's reference timestamp is the C3 candle close (`C3 open + 300 seconds`). The last consumed H1 BOS timestamp is persisted so a later setup cannot reuse it after a terminal state or restart.

## 9. Versioned experiment variants

- B0: legacy original EA filter logic, preserved only as a historical comparator; any technical repair changing signals is documented. Baseline outputs already checked into upstream are not accepted as performance evidence.
- B1: same baseline framework and comparable exits/risk, plus frozen sweep/reclaim condition.
- B2: B1 plus the frozen post-sweep displacement and CHoCH/MSS condition.
- B3: B2 plus displacement-created three-candle FVG and midpoint limit entry; this is the target implementation.
- Risk, costs, data interval, and comparable exits are shared wherever the signal model permits. Any unavoidable difference is recorded. Parameters/thresholds in this document are frozen before OOS evaluation.
