# Broker Configuration and Risk Calculation (XAUUSD / HFM Pro)

**Status: NOT VERIFIED.** No HFM Pro account, symbol specification, or commission/swap
schedule was available in the environment where this code was written. Every number
below is therefore either (a) a value the code *reads from the symbol at runtime*, or
(b) an input the operator must supply from measured broker evidence.

Do not treat any figure in this file as an HFM Pro fact until it is captured with the
probe described in §2 and pasted back here.

## 1. What the EA reads from the symbol at runtime

The EA never hardcodes a pip, contract size, tick value, or lot step. It reads these
`SYMBOL_*` properties and fails closed when any is missing or non-positive
(`SMCValidateConfiguration` in `Include/SMC_Config.mqh`):

| Property | Used for |
|---|---|
| `SYMBOL_TRADE_TICK_SIZE` | price normalization (floor/ceil/round), sweep threshold, gap minimum, FVG midpoint |
| `SYMBOL_TRADE_TICK_VALUE_LOSS` / `_PROFIT` / `SYMBOL_TRADE_TICK_VALUE` | monetary value of one tick |
| `SYMBOL_TRADE_TICK_VALUE_PROFIT` | reward side of net R:R |
| `SYMBOL_VOLUME_MIN` / `_MAX` / `_STEP` | volume flooring; below-minimum rejects rather than uplifts |
| `SYMBOL_TRADE_STOPS_LEVEL` | minimum entry/SL/TP separation |
| `SYMBOL_TRADE_FREEZE_LEVEL` | minimum distance from the live market |
| `SYMBOL_POINT` / `SYMBOL_DIGITS` | stop/freeze distance conversion |
| `SYMBOL_FILLING_MODE` | closing fills (FOK → IOC → RETURN, in that order of preference) |
| `SYMBOL_TRADE_EXEMODE` | logged at submission; not silently assumed |
| `SYMBOL_TRADE_MODE` | rejects `DISABLED` and `CLOSEONLY` |

Monetary risk is computed twice and cross-checked:

1. `OrderCalcProfit(order_type, symbol, volume, entry, stop)` — the native calculation.
2. `(abs(entry - stop) / tick_size) * tick_value` — the broker tick specification.

If the two disagree by more than 10% relative, `CSMCRiskManager::CalculateVolume`
returns `false` and the setup is rejected. This is the guard against assuming a
conventional XAUUSD pip or contract size on an unverified symbol.

## 2. How to capture real HFM Pro values

1. Log in to **HFM Pro demo** in MetaTrader 5 and confirm the exact gold symbol name
   (it is often not literally `XAUUSD`; it may carry a broker suffix).
2. Open the Strategy Tester, add `Scripts/SMC_SymbolSpec.mq5`, set `InpSymbol` to that
   exact symbol, and run it on any timeframe.
3. Copy the full output from the Experts log and paste it below.
4. Record the account server, account type (Cent/Standard), and commission/swap
   schedule shown in the account's contract specification.

### Captured values

```
NOT CAPTURED — no HFM Pro account was available in this environment.
```

| Property | Measured value |
|---|---|
| Symbol name | NOT VERIFIED |
| Account server | NOT VERIFIED |
| Digits / point / tick size | NOT VERIFIED |
| Tick value (profit / loss) | NOT VERIFIED |
| Contract size | NOT VERIFIED |
| Volume min / max / step | NOT VERIFIED |
| Stops level / freeze level | NOT VERIFIED |
| Filling mode flags | NOT VERIFIED |
| Swap long / short | NOT VERIFIED |
| Commission schedule | NOT VERIFIED |

### Historical-data note

Working memory from a previous, separate effort recorded that on **HFM Demo4** the
symbol history reached 2017-10-11 for M5 and that multi-year historical Bid/Ask ticks
were **not** demonstrated (`CopyTicksRange` returned `NO_DATA` for six historic one-hour
windows while a recent one-hour control worked). Those are Demo4 observations, not HFM
Pro facts, and they mean a broker-grade tick-accurate backtest is **not yet
demonstrated**. Bars alone may support a clearly labeled exploratory bar-model run,
never a broker-grade OOS claim (`research/experiment.py` enforces this).

## 3. Risk calculation, frozen

- Risk per trade: 0.25 %–0.50 % of the **stable broker-day equity reference**,
  default 0.50 %. Values outside that range are rejected at init.
- The daily equity reference is persisted in a terminal global variable per broker
  server day (`SMC_EQ_<login>_<magic>_<symbol>_<yyyymmdd>`), plus a day marker
  (`SMC_DAY_...`) so that a same-day restart cannot silently reset the cap. If the
  marker says the day was already initialized and the reference is gone, the EA fails
  closed rather than re-baselining.
- Daily loss cap: 2 % of that reference. On reaching it, EA-owned pending entry orders
  are cancelled and new orders are blocked. An open position is **not** closed solely
  because of the cap; its server-side SL/TP continues to apply.
- Two consecutive completed losing EA-owned position lifecycles block new entries until
  the next broker server day. A lifecycle is "completed" only when its exit volume
  covers its entry volume and the position identifier is no longer open. Any
  `DEAL_ENTRY_INOUT` reversal deal makes the reconstruction fail closed.
- Volume: `floor(risk_budget / (loss_per_lot + cost_per_lot), volume_step)`, capped at
  `volume_max`. If the floored volume is below `volume_min`, the setup is **rejected**;
  the EA never raises volume to the minimum. Final normalized risk is recomputed with
  `OrderCalcProfit` and must stay within budget (+0.01 rounding tolerance).
- Loss streak, daily loss, and realized PnL come from actual deal history filtered by
  magic number and symbol. Nothing is synthesized as "+3R"/"-1R".

## 4. Transaction costs

Pre-trade eligibility requires a **verified** cost model. `InpSMCCostModelVerified`
defaults to `false`, so the EA refuses to trade until the operator supplies measured
values:

- `InpSMCRoundTurnCommissionPerLot` — account-currency round-turn commission + fees per
  lot, taken from the broker contract specification.
- `InpSMCRoundTurnSlippagePerLot` — a deliberately conservative per-lot slippage
  allowance, modelled separately because it cannot be known before entry.

Net R:R is `(reward − cost) / (risk + cost)` where `cost` is
`(spread / tick_size) * max(tick_loss, tick_profit) * volume` plus the configured
round-turn commission and slippage. If net R:R < 3.00 the setup is rejected. The EA
never moves the target to force the ratio and never widens the stop to satisfy it.

Spread gate: `Ask − Bid` at submission must be ≤ **0.50** in XAUUSD price units.

## 5. Emergency risk policy

If, after a fill, the actual position volume and open price imply a stop risk larger
than the risk budget (for example because a partial fill or adverse slippage changed
the economics), `EnforcePositionRisk` in the EA calls
`CSMCExecution::EmergencyClose` once, using a broker-supported closing fill mode, and
logs `emergency_close_submitted` with the retcode. This is the only code path allowed
to close a position, and it never fires because of the daily entry cap.
