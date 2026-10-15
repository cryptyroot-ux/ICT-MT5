# SMC-H1M5-SweepFVG — V2 Parity Specification

> Status: **experimental candidate**, not a validated strategy.
> Frozen B3 rules live in `docs/SMC_H1M5_SWEEPFVG_SPEC.md` and are unchanged by this document.

## 0. What V2 is

V2 is the same structural strategy (H1 close-confirmed BOS → M5 liquidity sweep →
post-sweep CHoCH displacement → three-candle FVG → pending midpoint limit) with its
**causality, execution parity and safety** repaired. No threshold was tuned, and no
result in this document is a profitability claim.

Identity is separate from B3 so their histories can never be mixed:

| Field | B3 (frozen) | V2 |
|---|---|---|
| version | `1.0.0-research` | `2.0.0-parity` |
| variant | `B3` | `V2_PARITY` |
| magic | `15101501` | `15101602` |
| event log | `SMC_H1M5_SweepFVG_events.csv` | `SMC_H1M5_SweepFVG_V2_events.csv` |
| runtime config | — | `MQL5/Files/SMC_V2.cfg` |

## 1. Causality rules

1. **Premium/discount** uses the last *previous* completed server day
   (`CopyRates(symbol, PERIOD_D1, 1, 1, ...)`). The current day's extremes are never
   used. The earlier V1/V2 study that showed positive expectancy was invalid because
   it sized the day range with data from the decision day itself.
2. **Session filter** averages bar range per server hour over the last **200 closed M5
   bars** (`CopyRates(symbol, PERIOD_M5, 1, 200, ...)`). The forming bar is excluded.
   A short window is a rejection, never a silent "allow all".
   The result is cached per decision bar so it is computed once per M5 bar.
3. **Order timing**: an order is decided on the first bar *after* the FVG C3 has closed
   (`order_decision_index = c3_index + 1`). The pending limit can never fill on a bar
   that preceded its submission.
4. **H1 BOS confirmation** is the *close* of the breaking bar. The shared research core
   records the bar open, so V2 re-bases it; the frozen B3 core is left untouched.
5. **Pivots** must be confirmed by closed bars before they can be swept or broken, and
   equal pool levels within 2 ticks are one liquidity level anchored on the newest
   confirmed pivot. Differing levels reclaimed in the same bar are ambiguous and
   rejected.

## 2. Protection (single source of truth)

`SMC_ProtectionLevels` (MQL) and `protection_levels` (Python) are the same function:

```
raw_stop    = sweep_extreme - 0.5 * ATR   (BUY)
raw_stop    = sweep_extreme + 0.5 * ATR   (SELL)   <- sign is part of the contract
raw_distance= |entry - raw_stop|
distance    = max(raw_distance, 20.0, 3.0 * ATR)    <- widened, never tightened
stop_loss   = entry -/+ distance
take_profit = entry +/- 3 * distance                 <- fixed 3R, never next liquidity
```

Both are checked at start-up against the golden vector
`BUY 2000/1995/ATR 2 -> 1980/2060` and `SELL 2000/2005/ATR 2 -> 2020/1940`.
A mismatch blocks `OnInit`. Execution modules validate and round the supplied levels
but must never recompute them from the B3 raw formula.

## 3. Costs and the observe-only rule

Commission and slippage for the active HFM Demo account are **not verified**. Therefore:

- `COST_VERIFIED=0` is only accepted together with `OBSERVE_ONLY=1`.
- In observe-only the EA logs one `observe_only_candidate` record with the proposed
  limit/SL/TP and then ends the setup. `SubmitMidpointLimit`, `OrderCheck` and
  `OrderSend` are unreachable.
- `CalculateVolume`, `CalculateNetRewardRisk` and `CanOpenTrade` stay fail-closed while
  costs are unverified, even in observe-only.

Shipping config (`SMC_V2.cfg`):

```ini
SYMBOL=XAUUSD
MAGIC=15101602
RISK_PCT=0.25
DAILY_LOSS_PCT=2.0
COST_VERIFIED=0
OBSERVE_ONLY=1
COMMISSION_PER_LOT=0.0
SLIPPAGE_PER_LOT=10.0
LOG_LEVEL=2
```

## 4. Evidence status

The bar model is **not tick accurate**: entries use per-bar spread snapshots and a
separate slippage assumption. The DEV diagnostic
(`research/results/v2_parity_dev_20261016.json`, 2018-07-01 → 2024-04-23) produced
4 candidates and 2 filled trades. That is far below the frozen acceptance gate of 100
trades, so `acceptance_status` stays `INCONCLUSIVE`.

The 2024-04-23 → 2026-10-15 interval was viewed during the earlier study and is
**burned**: it may not be used for selection or as an out-of-sample claim.