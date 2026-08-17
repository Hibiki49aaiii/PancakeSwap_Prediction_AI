# v0.7 Alpha Research Contract

## Target

The model target is the settlement outcome of the PancakeSwap Prediction round:

`P(closePrice > lockPrice | information observable by the decision cutoff)`

The target is **not** Binance direction, the next Chainlink tick, or the final Pancake pool share. Those are candidate explanatory variables or intermediate hypotheses only.

## Initial alpha family

v0.7 formalizes the first settlement-source-first feature family:

- Binance spot versus latest observed Chainlink price
- Binance perpetual versus latest observed Chainlink price
- spot/perpetual basis
- aggressive spot and perpetual order-flow imbalance
- current Chainlink observation age
- empirical Chainlink update hazard derived from completed past update intervals only

Every source observation carries its own event timestamp. Feature construction fails closed if any input timestamp is later than the decision timestamp.

## Oracle update hazard

For current oracle age `a` and prediction horizon `h`, the dependency-free baseline estimates:

`P(interval <= a+h | interval >= a)`

from completed historical update intervals. It is deliberately simple and exists as an auditable baseline before adding survival models or ML.

## Calibration

Raw model probabilities are not accepted directly by the EV engine. v0.7 adds a dependency-free histogram reliability calibrator with shrinkage toward the training base rate. Calibration training must be purged OOS and carry `train_max_epoch` provenance.

## Research ledger

Every candidate decision can be serialized canonically and SHA-256 hashed with market/epoch/decision timestamp, model and feature-set IDs, raw and calibrated probability, expected value, action, feature digest, and `train_max_epoch`.

The ledger is research evidence. It contains no key material and has no signing authority.

## ClickHouse

`sql/clickhouse/v0_7_core.sql` defines the first high-volume analytical schema for Binance trades, Chainlink updates, Pancake pool snapshots, feature rows, and research predictions. SQLite/on-chain raw evidence remains the immutable/reorg-aware source for BSC reconstruction; ClickHouse is the normalized analytical plane.

## Acceptance criteria before richer models

A richer model is rejected unless it beats the simpler baselines on purged walk-forward data with lower Brier score and/or better Brier skill, acceptable calibration error, positive cost-aware net EV, stable performance across time/regimes, and feature-ablation evidence that the claimed alpha family contributes out of sample.
