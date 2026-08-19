# PancakeSwap Prediction AI

Clean-room research and execution-readiness project for PancakeSwap Prediction on BNB Smart Chain.

## Canonical repository

This repository is the canonical source of truth for all work from v0.7 onward.

Previous local artifacts named `PancakePredictionResearch` are legacy development snapshots only. New architecture, implementation, tests, documentation, and release history belong here.

## Objective

Estimate the calibrated probability that a PancakeSwap Prediction round settles BULL or BEAR using only information observable before the decision cutoff, then evaluate economics only after realistic costs and execution effects.

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
| 0 | Historical data integrity | **Canonical v0.7 append-only hash-chained Event Store implemented; CI tested** |
| 1 | Deterministic replay | **Canonical observation-time replay and leakage cutoff implemented; CI tested** |
| 2 | Leakage-safe, cost-aware evaluation | **Canonical diluted pool EV + probabilistic metrics implemented; real dataset evaluation pending** |
| 3 | Purged walk-forward / OOS evaluation | **Canonical split generator implemented; real OOS evidence pending** |
| 4 | Paper / Shadow | **Durable decision/settlement ledger implemented; observed multi-round economics evidence still required** |
| 5A | Durable execution fault model | **Canonical v0.7 implementation complete; CI tested** |
| 5B | BSC fork execution | Harness implemented; **BLOCKED until observed real local-fork evidence is recorded** |
| 6A | Tiny-live readiness / safety preflight | v0.7 evidence gate implemented; cannot clear from assumed evidence |
| 6B | Actual funded validation | Not authorized / not implemented |
| 7 | Production | Not reached |

### Stage 0-1 data and replay invariants

The canonical research path now enforces:

- append-only SQLite event persistence;
- SHA-256 event hash chaining so silent historical mutation is detectable;
- unique event identifiers;
- separate source/event time and local observation time;
- deterministic replay ordered by observation time and ingest sequence;
- strict exclusion of anything first observed after the decision cutoff, even when the embedded source timestamp is older;
- latest-value and freshness helpers that operate only on a leakage-safe replay snapshot.

### Stage 2-3 evaluation invariants

The canonical evaluation path now includes:

- own-stake payout dilution in the winning-side denominator;
- configurable treasury fee and gas cost;
- configurable same-side/opposite-side post-decision pool movement assumptions;
- explicit execution-success probability instead of silently treating every candidate as filled;
- break-even probability calculation;
- Brier score, log loss and expected calibration error (ECE);
- chronological purged walk-forward splits with optional rolling training windows.

These modules are infrastructure, not evidence that a strategy is profitable. Real historical and out-of-sample data must still be ingested and evaluated.

### Stage 4 shadow invariants

The durable shadow ledger stores the decision-time record separately from the later settlement record. It prevents duplicate decisions/settlements for the same round, requires settlement after the recorded decision cutoff, and calculates simulated settlement economics from the later observed final pools.

A shadow evidence object can only be derived from an explicit evaluation policy and observed ledger summary. The repository does not contain observed multi-round profitability evidence yet.

### Stage 5A canonical fault model

The canonical implementation includes:

- explicit durable intent states;
- unresolved transaction recovery through `UNKNOWN` rather than false failure classification;
- mined-to-unknown rollback for reorg handling;
- same-intent replacement transaction tracking;
- terminal-state enforcement;
- SQLite WAL persistence with full synchronous writes;
- restart recovery of unresolved intents;
- unique nonce reservation across unresolved intents.

### v0.7 evidence rule

`src/pancake_prediction_ai/evidence_gate.py` enforces that Stage 6A can only become ready when all of the following are true:

- Stage 5A evidence is an observed pass;
- Stage 5B evidence is an observed pass from an actual local BSC fork;
- shadow economics evidence is an observed pass;
- kill switch, wallet binding, per-round cap and balance cap checks pass;
- no unresolved execution intents remain;
- the decision window is still open;
- signing and mainnet broadcasting remain disabled during the Stage 6A preflight.

`assumed` and `self_reported` evidence can never clear the gate. Evidence payloads are SHA-256 bound to prevent silent mutation.

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
