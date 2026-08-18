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

Every source observation carries both a source timestamp and an information-availability boundary. Feature construction fails closed if the information was not available before the decision cutoff, even when the source event itself is timestamped earlier.

## Active Chainlink oracle timeline

Historical collection may contain `AnswerUpdated` events from multiple Chainlink contracts because PancakeSwap Prediction can change its oracle over time. Research must never concatenate all of those feeds into one price series.

`historical-bootstrap` persists an oracle anchor from the historical `oracle()` state proven during archive preflight. `oracle_history.py` then replays canonical `NewOracle` events in exact block/transaction/log order and accepts an `AnswerUpdated` event only when its contract address was active at that exact position.

The anchor is intentionally applied starting from the next block. Events in the anchor block are accepted only when an explicit `NewOracle` event establishes the state before them. Missing anchor metadata, anchor/event disagreement, inactive old feeds, and orphaned reorg logs fail closed or are excluded.

Use:

`pcs-prediction oracle-history-report --market BNBUSD --db history.sqlite3`

before generating a research dataset.

## Information-availability lag

Historical event timestamps are not equivalent to information arrival timestamps.

- Binance websocket-style trades use `event_timestamp_ms` as the availability boundary.
- Binance public archives do not contain the original websocket arrival timestamp, so archive parsing requires an explicit `availability_lag_ms` assumption and reconstructs the availability boundary from trade time plus that lag.
- Chainlink observations become available no earlier than the block containing `AnswerUpdated`; research additionally supports an explicit `chainlink_availability_lag_ms` to model block propagation/processing latency.

Sub-second alpha is rejected if it disappears under realistic positive-lag scenarios. A zero-lag run is an optimistic reference case, not sufficient profitability evidence.

## Verified Binance public archives

`binance_archive.py` normalizes official aggTrades archives and handles the Spot timestamp unit change to microseconds from 2025-01-01 onward. Futures millisecond archives remain supported separately.

Every official ZIP should be verified against its companion `.CHECKSUM` before use. The normalized provenance records the archive SHA-256, venue, symbol, timestamp-unit rule, explicit availability lag, row count, timestamp range, and aggregate-trade ID range.

Use:

`pcs-prediction binance-archive-inspect --market BNBUSD --archive <zip> --checksum <zip.CHECKSUM> --venue spot --timestamp-unit auto --availability-lag-ms <N>`

The command verifies the checksum before parsing and prints only normalized provenance, not credentials or RPC configuration.

## Oracle update hazard

For current oracle age `a` and prediction horizon `h`, the dependency-free baseline estimates:

`P(interval <= a+h | interval >= a)`

from completed historical update intervals. It is deliberately simple and exists as an auditable baseline before adding survival models or ML.

## Canonical research dataset path

The safe path is:

`historical DB -> canonical Prediction events -> active-oracle Chainlink events -> bounded Binance ClickHouse windows -> PoolFeatureRow -> ResearchFeatureRow`

`research_inputs.py` owns the canonical SQLite/replay/oracle loader. `clickhouse_dataset.py` then supplies Binance Spot/Perp data in bounded time chunks. Low-level feature functions remain testable independently, but production research campaigns should not manually concatenate raw Chainlink feeds or materialize all Binance history in Python.

Pool-history feature construction uses an incremental settlement cursor for normal epoch-ordered replay rather than rescanning all prior rounds for every row. Non-monotonic replay falls back to the slower reference implementation instead of silently changing semantics.

The ClickHouse-backed builder groups decision rows by time chunk. The default chunk span is one hour. Each chunk loads Spot once and Perpetual once, then reuses those indexed trades for all decision rounds inside the chunk. This avoids a one-round/two-query N+1 pattern while keeping Python memory bounded to one chunk of exchange data plus replay/feature state.

`pcs-clickhouse dataset-summary` is the standard command-line bridge from canonical SQLite history to chunked ClickHouse data. It prints input/replay/oracle digests, all timing assumptions, feature-row counts, chunk count, and maximum Spot/Perp rows held for one chunk. It intentionally does not dump the feature matrix.

## Calibration

Raw model probabilities are not accepted directly by the EV engine. v0.7 adds a dependency-free histogram reliability calibrator with shrinkage toward the training base rate. Calibration training must be purged OOS and carry `train_max_epoch` provenance.

## Research ledger and campaign manifest

Every candidate decision can be serialized canonically and SHA-256 hashed with market/epoch/decision timestamp, model and feature-set IDs, raw and calibrated probability, expected value, action, feature digest, and `train_max_epoch`.

The ledger is research evidence. It contains no key material and has no signing authority.

`research_manifest.py` additionally binds each campaign to replay input/output digests, the oracle anchor/timeline, verified Binance archive hashes, and all timing assumptions including Chainlink availability lag. Changing a source or latency assumption changes the deterministic manifest digest.

## ClickHouse

`sql/clickhouse/v0_7_core.sql` defines the high-volume analytical schema for Binance trades, Chainlink updates, Pancake pool snapshots, feature rows, and research predictions. SQLite/on-chain raw evidence remains the immutable/reorg-aware source for BSC reconstruction; ClickHouse is the normalized analytical plane.

Binance archive ingestion is bounded-memory. `clickhouse.py` verifies the official checksum before any insert, streams the ZIP/CSV row by row, and sends bounded JSONEachRow batches instead of materializing the archive. The default batch size is 50,000 rows.

The Binance table uses `ReplacingMergeTree(ingest_version)` keyed by venue, symbol, latency assumption, and aggregate-trade ID. This makes interrupted/repeated archive loads convergent after merges. Research reads use `FINAL` so retry duplicates cannot temporarily inflate order flow before background merging finishes.

The previous v0.7 `MergeTree` shape is not silently accepted. `pcs-clickhouse schema-check` verifies the engine and required provenance/latency/version columns before either ingest or research reads. Existing old tables must be migrated or recreated explicitly.

Trade-window values are sent through ClickHouse typed query parameters rather than interpolated into SQL strings.

Connection secrets are supplied through environment variables and are not printed:

```bash
export CLICKHOUSE_URL='http://127.0.0.1:8123'
export CLICKHOUSE_DATABASE='default'
export CLICKHOUSE_USER='default'
export CLICKHOUSE_PASSWORD='...'

pcs-clickhouse ping
pcs-clickhouse schema-check

pcs-clickhouse binance-ingest \
  --market BNBUSD \
  --archive BNBUSDT-aggTrades-2026-08-01.zip \
  --checksum BNBUSDT-aggTrades-2026-08-01.zip.CHECKSUM \
  --venue spot \
  --timestamp-unit auto \
  --availability-lag-ms 25

pcs-clickhouse binance-window \
  --market BNBUSD \
  --venue spot \
  --availability-lag-ms 25 \
  --start-ms 1785542400000 \
  --end-ms 1785542460000

pcs-clickhouse dataset-summary \
  --market BNBUSD \
  --db artifacts/bnbusd-history.sqlite \
  --spot-availability-lag-ms 25 \
  --perp-availability-lag-ms 40 \
  --chainlink-availability-lag-ms 500
```

A latency assumption is part of the stored logical key. Separate zero-lag and positive-lag campaigns therefore remain distinct and can be compared without rewriting the same rows in place.

## Acceptance criteria before richer models

A richer model is rejected unless it beats the simpler baselines on purged walk-forward data with lower Brier score and/or better Brier skill, acceptable calibration error, positive cost-aware net EV, stable performance across time/regimes, and feature-ablation evidence that the claimed alpha family contributes out of sample.

A profitability claim must also survive sensitivity checks for Binance availability lag, Chainlink block-availability lag, gas, own-stake dilution, post-decision pool growth, and execution latency. Zero-lag or final-pool benchmarks may be reported only as explicitly infeasible/optimistic references.
