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
| 0 | Historical data integrity | Implemented foundation; real full-history validation pending archive-capable BSC RPC |
| 1 | Deterministic replay | Implemented and unit-tested foundation |
| 2 | Leakage-safe, cost-aware backtest | Implemented and unit-tested foundation |
| 3 | Purged walk-forward / OOS evaluation | Implemented and unit-tested foundation |
| 4 | Paper / Shadow | Partial research-ledger foundation; operational long-running shadow campaign not yet completed |
| 5A | Durable execution fault model | Fork-only durable intent/reconciliation state machine implemented and adversarially unit-tested |
| 5B | BSC fork execution | Loopback-only transaction adapter, Pancake Bull/Bear intent encoding, and fixed-block bet preflight implemented; actual local BSC-fork campaign pending |
| 6A | Tiny-live readiness / safety preflight | Not implemented as an executable live gate |
| 6B | Actual funded validation | Not authorized / not implemented |
| 7 | Production | Not reached |

Infrastructure assumptions and green unit tests are never treated as evidence of profitability. Real historical integrity, shadow economics, out-of-sample performance, local-fork recovery drills, and any separately authorized funded validation must be demonstrated independently.

Stage 5's explicit safety contract and exit criteria are documented in [`docs/STAGE5_FORK_EXECUTION.md`](docs/STAGE5_FORK_EXECUTION.md).

## CLI

After installation, the package exposes:

```bash
pcs-prediction status
```

Historical collection remains read-only and uses a BSC JSON-RPC endpoint:

```bash
export BSC_RPC_URL='...'
pcs-prediction historical-bootstrap \
  --market BNBUSD \
  --db artifacts/bnbusd-history.sqlite
```

The transaction-capable Stage 5 adapter is intentionally not wired to the mainnet historical RPC path. It accepts loopback local-fork endpoints only and has no private-key signing path.

Before any fork bet is submitted, the CLI performs a fixed-block read-only Prediction preflight. The standalone command is:

```bash
pcs-prediction fork-bet-preflight \
  --fork-rpc-url http://127.0.0.1:8545 \
  --db artifacts/fork-execution.sqlite3 \
  --intent-id 1
```

The preflight checks current epoch, strict round timing, pause state, minimum bet, existing wallet bet, EOA compatibility, and stake balance. `fork-submit-intent` repeats the same check and fails before nonce reservation or send if it is not ready.

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
