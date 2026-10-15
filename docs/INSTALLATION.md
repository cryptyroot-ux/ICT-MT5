# Installation and Operation — SMC-H1M5-SweepFVG

**Readiness: RESEARCH / DEMO ONLY.** This EA has not passed out-of-sample and
execution-risk validation, and compilation status is tracked in
`docs/VALIDATION_REPORT.md`. It is deliberately **hard-blocked on real accounts**
(`OnInit` returns `INIT_FAILED` when `ACCOUNT_TRADE_MODE == ACCOUNT_TRADE_MODE_REAL`).

## 1. Install

```bash
git clone https://github.com/cryptyroot-ux/ICT-MT5.git
cd ICT-MT5
git switch feature/smc-h1m5-sweepfvg
```

Copy into your MetaTrader 5 **data folder**:

| Source | Destination |
|---|---|
| `Experts/SMC_H1M5_SweepFVG.mq5` | `MQL5/Experts/SMC_H1M5_SweepFVG.mq5` |
| `Include/SMC_*.mqh` | `MQL5/Include/` |
| `Scripts/SMC_SymbolSpec.mq5` | `MQL5/Scripts/SMC_SymbolSpec.mq5` |

`Experts/ICT_EA.mq5` and `Include/ICT_*.mqh` are the preserved upstream baseline. They
are **not** used by the SMC EA and are kept only for audit/B0 reference. Note that the
baseline does not compile as received (see `docs/SOURCE_AUDIT.md`, AUD-001); do not
attach it.

## 2. Compile

Open MetaEditor, compile `SMC_H1M5_SweepFVG.mq5`. Expect zero errors and zero warnings
from the `SMC_*` sources. Record the actual build number and result in
`docs/VALIDATION_REPORT.md` — do not copy a claim from another environment.

## 3. Measure the broker symbol first

Run `Scripts/SMC_SymbolSpec.mq5` on the **exact** HFM Pro demo symbol and paste the
output into `docs/BROKER_CONFIGURATION.md` §2. Until that is done the broker
configuration is NOT VERIFIED.

## 4. Configure inputs

| Input | Default | Meaning |
|---|---:|---|
| `InpSMCSymbol` | `XAUUSD` | Exact broker gold symbol name |
| `InpSMCMagic` | `15101501` | Dedicated magic number; change if it collides |
| `InpSMCRiskPercent` | `0.50` | Risk % of the daily equity reference (allowed 0.25–0.50) |
| `InpSMCDailyLossPercent` | `2.0` | Frozen daily loss cap; other values are rejected |
| `InpSMCCostModelVerified` | `false` | Must be `true` and backed by measured costs |
| `InpSMCRoundTurnCommissionPerLot` | `0.0` | Measured round-turn commission+fees per lot |
| `InpSMCRoundTurnSlippagePerLot` | `0.0` | Conservative slippage allowance per lot |
| `InpSMCLogLevel` | `SMC_LOG_INFO` | `ERROR` / `INFO` / `DEBUG` |

Attach to an **M5** chart of the configured symbol. The EA rejects any other chart
timeframe at init, so M5 execution and H1 context cannot be mixed up accidentally.

## 5. Verify before trusting

1. `InpSMCCostModelVerified=false` must produce `configuration_rejected` with reason
   `verified_nonnegative_cost_model_required` and `INIT_PARAMETERS_INCORRECT`.
2. With a verified cost model but a symbol missing trade properties, init must fail
   closed, not trade.
3. In the journal, confirm `initialized` appears and no `entries_blocked` reason is
   unexpected.
4. Confirm `MQL5/Files/SMC_H1M5_SweepFVG_events.csv` is created with a header row.

## 6. Run the deterministic test suite

```bash
python3 -m unittest discover -s tests -v
```

These are deterministic logic and static-structure tests. They are **not** MQL5
compile tests and **not** MT5 runtime tests. Both of those are separate gates.

## 7. Run the B0–B3 experiment

The runner refuses to produce OOS claims without broker evidence:

```bash
python3 -m research.experiment   # after adding a CLI entry point / manifest
```

Required inputs are a broker tick/bar export plus a `DataManifest` whose `sha256`
matches the file byte-for-byte. `source_kind="synthetic"`, `source_kind="third_party"`,
and `data_type="bar_model"` are all rejected for OOS claims. B0 additionally cannot be
produced by the Python harness at all: it needs a documented repair of the legacy EA
plus a MetaEditor/Strategy Tester run.

## 8. Interpret the event log

`SMC_H1M5_SweepFVG_events.csv` columns include `setup_id`, `event`, `state_before`,
`state_after`, `h1_bos_level`, `liquidity_level`, `sweep_extreme`, `choch_level`,
`fvg_lower`/`fvg_upper`, `entry`, `sl`, `tp`, `atr14_m5`, `spread`, `risk_percent`,
`volume`, `expected_risk`, `net_rr`, `order_ticket`, `deal_id`, `position_id`,
`fill_price`, `slippage`, `retcode`, `actual_pnl`, `commission`, `swap`, `fee`, and
`reason`. Every rejection, acceptance, expiry, cancellation, and lifecycle completion
carries a `reason`.

`config_hash` is an FNV-1a hash of the strategy name, version, variant, symbol, magic,
risk, spread limit, and cost inputs, so a run can be tied to its exact configuration.

## 9. Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| `configuration_rejected` / `verified_nonnegative_cost_model_required` | costs not supplied | measure and set the cost inputs, then flip the verified flag |
| `configuration_rejected` / `execution_chart_must_be_M5` | wrong chart timeframe | attach to M5 |
| `initialization_failed` / `real_accounts_are_disabled...` | real account | use demo only |
| `order_rejected` / `spread_exceeds_0.50_price_units` | spread too wide | wait; do not widen the limit |
| `order_rejected` / `monetary_risk_or_volume_properties_invalid` | volume below broker minimum or tick/cross-check disagreement | confirm contract size/tick value for this account |
| `order_rejected` / `net_reward_risk_below_1_to_3_or_costs_unavailable` | next liquidity target too close after costs | no trade; this is correct behavior |
| `entries_blocked` / `daily_loss_or_consecutive_loss_entry_block` | risk guard active | inspect ledger; entries resume next broker day |
