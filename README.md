# PancakeSwap Prediction AI

Clean-room research and execution-readiness project for PancakeSwap Prediction on BNB Smart Chain.

## Canonical repository

This repository is the canonical source of truth for all work from v0.7 onward.

Previous local artifacts named `PancakePredictionResearch` are legacy development snapshots only. New architecture, implementation, tests, documentation, and release history belong here.

## Objective

Estimate the calibrated probability that a PancakeSwap Prediction round settles BULL or BEAR using only information observable before the decision cutoff, then act only when expected value remains positive after realistic costs and execution effects.

Primary target:

`P(BULL settlement | information available at decision time)`

Economic evaluation must include at minimum:

- PancakeSwap participation/treasury fee
- gas
- own-bet payout dilution
- post-decision pool movement
- transaction latency and missed-window risk
- execution/reconciliation uncertainty

Prediction accuracy alone is not a profitability criterion.

## Core architecture

```text
Binance Spot / Futures
        |
BSC + PancakeSwap + Chainlink
        |
Immutable Event Store
        |
Replay / Data Quality
        |
Feature Families + Regime
        |
Probability Model
        |
Calibration
        |
Expected Value Engine
        |
Risk Engine
        |
Durable Decision / Intent
        |
Shadow -> Fork -> Tiny Live gate
```

## Research priorities

1. Binance / Chainlink lead-lag and divergence
2. Chainlink freshness and update-hazard features
3. Binance spot/perpetual order flow
4. Pancake pre-lock pool flow
5. volatility / regime features
6. execution-quality and latency features
7. calibrated probability ensembles
8. purged walk-forward and feature ablation

## Validation stages

| Stage | Purpose | Current status |
|---|---|---|
| 0 | Historical data integrity | Implemented foundation |
| 1 | Deterministic replay | Implemented foundation |
| 2 | Leakage-safe, cost-aware backtest | Implemented foundation |
| 3 | Purged walk-forward / OOS evaluation | Implemented foundation |
| 4 | Paper / Shadow | Implemented foundation |
| 5A | Durable execution fault model | Implemented and tested locally |
| 5B | BSC fork execution | Harness ready; environment previously assumed for continued development |
| 6A | Tiny-live readiness / safety preflight | Implemented foundation |
| 6B | Actual funded validation | Not authorized / not implemented |
| 7 | Production | Not reached |

Infrastructure assumptions are never treated as evidence of profitability. Shadow economics, out-of-sample performance, and any funded validation must be demonstrated separately.

## Reference-design policy

Existing Polymarket and prediction-market repositories are reference material, not the codebase to fork.

High-value reference concepts currently include:

- PolyWeather: settlement-source-first probability modeling and calibration
- ent0n29/polybot: research pipeline and separation of ingestion/analytics/execution
- Polymarket_data: feature-hypothesis research from large-scale participant behavior
- PydanticAI: typed research-agent orchestration only
- CloddsBot: trade ledger, risk and experiment-tracking concepts

Third-party wallet management, private-key handling, live executors, Polymarket CLOB assumptions, future-leaking final pool features, and LLM signing authority are excluded.

## Security boundary

Research and model layers must not hold private keys or signing authority.

AI/LLM components may assist with research, feature analysis, model evaluation and explanation. They are not wallet controllers.

## Development rule

All future work should be performed against `Hibiki49aaiii/PancakeSwap_Prediction_AI` using branches and reviewable commits. Do not continue development in the former standalone `PancakePredictionResearch` repository name.
