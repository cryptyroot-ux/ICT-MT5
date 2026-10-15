# SMC-H1M5-SweepFVG Design

## Objective and constraints

Implement the user's frozen H1 BOS → M5 liquidity sweep/reclaim → post-sweep displacement/CHoCH → displacement-created three-candle FVG → midpoint limit entry strategy for XAUUSD. Keep the original ICT EA and modules as an auditable legacy comparator; add a distinctly named strategy EA and modules rather than silently changing the meaning of the original EA. Use broker symbol properties, actual deal history, deterministic state transitions, fail-closed behavior, structured CSV events, and reproducible validation. Preserve all user-provided B0–B3 and OOS thresholds exactly.

## Chosen implementation approach

1. **Recommended and selected:** additive SMC implementation beside the unmodified legacy implementation. This preserves upstream behavior for audit, makes the target EA independently installable, and prevents accidental substitution of legacy D1/kill-zone/OB logic for the requested H1/M5 flow.
2. In-place rewrite of the original files: rejected because it would erase a traceable upstream baseline and make B0 comparisons ambiguous.
3. Python-only implementation: rejected as the final EA because it cannot execute trades in MetaTrader 5; a Python reference harness is limited to deterministic logic and experiment-data validation.

## Frozen mechanics

The detailed executable choices are in `docs/SMC_H1M5_SWEEPFVG_SPEC.md`: span-2 confirmed pivots, close-only BOS, two-tick equal-level tolerance, two-tick sweep threshold, three-candle reclaim window, 1 ATR / 0.60 body-range / outer-quartile displacement, displacement as FVG C2, midpoint entry, two-times-ATR-buffered sweep extreme stop formula (`0.5×ATR(14)`), next opposing liquidity target, net 3R minimum, six bars/30 minutes expiry, risk 0.25–0.50% default 0.50%, 2% daily cap, two consecutive losses, and 0.50 XAUUSD spread cap. The specification is fixed before any OOS results.

## Components and boundaries

- EA lifecycle/config/events: `Experts/SMC_H1M5_SweepFVG.mq5`.
- Immutable strategy/rule constants and validation: `Include/SMC_Config.mqh`.
- Completed H1 swing/BOS bias: `Include/SMC_MarketStructure.mqh`.
- M5 liquidity pivots, sweep excursion/reclaim, target pools: `Include/SMC_Liquidity.mqh`.
- M5 ATR displacement and post-sweep internal structure break: `Include/SMC_Displacement.mqh`.
- Chronological three-candle FVG validity: `Include/SMC_FVG.mqh`.
- Setup identity and explicit lifecycle: `Include/SMC_SetupStateMachine.mqh`.
- Monetary risk and daily guardrails: `Include/SMC_RiskManager.mqh`.
- Broker-aware pending order lifecycle: `Include/SMC_Execution.mqh`.
- Position-lifecycle trade history/recovery: `Include/SMC_TradeLedger.mqh`.
- Machine-readable event records: `Include/SMC_Logger.mqh`.
- Reference-model tests and data-gated B0–B3 research runner: `tests/`, `research/`.
- Audit, broker/risk, test, installation, experiment, and validation evidence: `docs/`.

## Data flow and fail-closed behavior

Only closed rates enter detectors. A setup cannot advance without the immediately preceding event and unique setup identity. The EA reconciles owned orders, positions, and deals before enabling new entries. If symbol properties, cost configuration, history, state, target, price/volume constraints, or risk cannot be validated, no order is sent. `OrderSend` return status is followed by retcode and actual terminal/history state reconciliation. The pending order is cancelled on expiry or invalidation; a filled position is managed separately.

## Validation boundary

Python deterministic tests exercise the reference logic and experiment data contract; they are not MQL5 compiler/runtime tests. MetaEditor compile and MT5 Strategy Tester are separate gates. Existing upstream synthetic/Yahoo/randomized artifacts are excluded from performance evidence. HFM Pro configuration and historical ticks require verified broker data; absent evidence means NOT VERIFIED/INCONCLUSIVE.
