# Quantitative Experiment Protocol

## Frozen gates (unchanged)

Primary out-of-sample gates from the user specification:

| Measure | Threshold | Current status |
|---|---:|---|
| Net expectancy | ≥ +0.25R/trade | INCONCLUSIVE — no valid target-strategy OOS run yet |
| Profit factor | ≥ 1.50 | INCONCLUSIVE — no valid target-strategy OOS run yet |
| Maximum equity drawdown | < 12% | INCONCLUSIVE — no valid target-strategy OOS run yet |
| Completed trades | ≥ 100 | INCONCLUSIVE — no valid target-strategy OOS run yet |
| Mandatory risk limits | all correct | NOT VERIFIED until MQL5 runtime validation |
| Critical execution/accounting defects | none unresolved | NOT VERIFIED until MetaEditor/Strategy Tester checks |

These thresholds must not be relaxed or redefined after results are viewed. Fewer than 100 completed trades is INCONCLUSIVE, not a pass.

## Variants and common controls

Run frozen B0, B1, B2, and B3 as separately identified variants. Each run records the strategy source/version hash, model variant, exact inputs/config hash, data manifest/hash, account currency and starting equity, broker/symbol properties, timezone, bar/tick model, spread, commission, swap, and slippage. Use the same risk and cost model and comparable exit assumptions across variants where defined. B3 uses the liquidity target and order behavior in `SMC_H1M5_SWEEPFVG_SPEC.md`.

Use chronological in-sample / validation / untouched out-of-sample splits and walk-forward windows. Freeze parameters before OOS. A setup/order/position crossing a split is excluded from both segments (purge boundary) and recorded; never carry state or an outcome from train into test.

Prefer HFM Pro XAUUSD real ticks. Broker OHLC may be used for a clearly labeled exploratory bar-model run, never described as tick-accurate. HFM Demo4 data, Yahoo `GC=F`, synthetic fixtures, and checked-in historical output CSVs are not substitutes for verified HFM Pro data.

## Required trade-derived metrics

For each segment and variant report completed trade count, win rate, average win/loss in R, net expectancy in R, profit factor, gross/net account-currency PnL, maximum equity drawdown and percent, maximum consecutive losing lifecycles, exposure/time in market, spread/commission/swap totals and averages, rejection reasons, year/session splits, and stress results. Define denominator and lifecycle classification. Compute from actual recorded/simulated fills and closes, never synthetic ±3R labels.

Use confidence intervals for expectancy and relevant ratios; use a block bootstrap for serial dependence when the sample supports it. Record seed, block length, resample count, and method. A CI does not overcome inadequate sample size. CSCV/PBO is optional and only reported if executed reproducibly.

## Stress matrix

Re-run the frozen model with exact configuration diffs: baseline spread, +50% spread, baseline slippage, +100% slippage, realistic verified commissions/swaps, adverse rejects/delayed fills/partial fills, and chronological volatile regimes. Broker-spec sensitivity runs are valid only with measured properties; do not fabricate specifications.

## Empirical finding that must be resolved before any OOS claim

Implemented exactly as frozen and measured on the only real HFM export available
(`docs/VALIDATION_REPORT.md` §4.2), the frozen stop rule and the frozen target rule are
mutually near-unsatisfiable: the next opposing confirmed liquidity level sits a median
of **0.06 R** from a sweep-extreme ± 0.5 × M5 ATR(14) stop, and only **0.10 %** of
events reach raw R ≥ 3. Net R:R ≥ 3 additionally requires `reward ≥ 3·risk + 4·cost`,
so any positive transaction cost makes acceptance strictly harder. B0–B3 therefore
produce statistically insignificant trade counts and every gate is INCONCLUSIVE.

Resolving this requires an explicit specification change and a re-freeze. Loosening a
threshold, widening a stop opportunistically, or switching to a manufactured target to
manufacture trade count is prohibited, because each of those changes the strategy while
leaving its name intact.

## Evidence currently available

- Upstream's historical Python experiments are not valid strategy results: they include synthetic generated prices, Yahoo gold futures rather than broker XAUUSD, seeded random outcomes, non-equivalent exits, missing costs, and/or look-ahead behavior (see `SOURCE_AUDIT.md`).
- Existing HFM working-memory observations are from Demo4 and do not establish HFM Pro contract/cost conditions. Historical Bid/Ask ticks were not shown to exist for the full bar interval.
- No B0/B1/B2/B3 Strategy Tester run was executed in this session. B0–B3 metrics and all statistical/stress gates therefore remain INCONCLUSIVE/NOT VERIFIED; no performance or profitability claim is made.
