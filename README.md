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

## v0.7 canonical foundation

The first canonical implementation slice now contains:

- current BNB Chain Prediction market registry for BNBUSD/BTCUSD/ETHUSD;
- pinned deployed V2 bet selectors and event topics;
- deterministic unsigned semantic bet intents;
- read-oriented JSON-RPC without any signer/private-key surface;
- loopback-only Anvil transaction RPC for future Stage 5B tests;
- deterministic unit tests and CI.

See `docs/V07_FOUNDATION.md` and `docs/SECURITY_BOUNDARY.md`.

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
| 0 | Historical data integrity | Canonical rebuild in progress |
| 1 | Deterministic replay | Legacy foundation; canonical rebuild pending |
| 2 | Leakage-safe, cost-aware backtest | Legacy foundation; canonical rebuild pending |
| 3 | Purged walk-forward / OOS evaluation | Legacy foundation; canonical rebuild pending |
| 4 | Paper / Shadow | Legacy foundation; canonical rebuild pending |
| 5A | Durable execution fault model | Legacy evidence preserved; canonical rebuild pending |
| 5B | BSC fork execution | Loopback RPC boundary re-established; observed fork evidence still required |
| 6A | Tiny-live readiness / safety preflight | Legacy evidence preserved; canonical rebuild pending |
| 6B | Actual funded validation | Not authorized / not implemented |
| 7 | Production | Not reached |

Infrastructure assumptions are never treated as evidence of profitability. Shadow economics, out-of-sample performance, and any funded validation must be demonstrated separately.

## Reference-design policy

Existing Polymarket and prediction-market repositories are reference material, not the codebase to fork.

Third-party wallet management, private-key handling, live executors, Polymarket CLOB assumptions, future-leaking final pool features, and LLM signing authority are excluded.

## Development rule

All future work should be performed against `Hibiki49aaiii/PancakeSwap_Prediction_AI` using branches and reviewable commits. Do not continue development in the former standalone `PancakePredictionResearch` repository name.
