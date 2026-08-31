# Source-Native Order Must Break Availability-Timestamp Ties
Status: observation
Date: 2026-08-22
Source case: `../cases/2026-08-20-v0-7-research-readiness/`
Confidence: high

## Context

The one-day economic smoke initially failed while aligning official Binance Spot and USD-M `aggTrades`. Multiple canonical trades can share the same millisecond availability timestamp while carrying different prices. Treating timestamp alone as the observation identity made those valid rows look contradictory.

## Observation

Availability time answers when information becomes usable; it does not necessarily define a total order among source events. Alignment must preserve a deterministic source-native ordering key and use it to break equal-timestamp ties.

For the current sources:

- Binance `aggTrades`: `aggregate_trade_id` is the source-native order;
- Chainlink logs: block number, transaction index, and log index form the source-native order;
- if two rows have the same availability timestamp but different source-order keys, the later source-order row is the deterministic as-of observation;
- only conflicting values at the same exact source identity/order key are ambiguous and must fail closed.

Do not invent an order from ingestion order, ClickHouse part order, file row position after transformations, or wall-clock execution timing.

## Evidence

The first real Aug 18 economic attempt exposed equal-millisecond Binance observations with distinct prices and failed closed rather than silently choosing one.

After alignment retained source-native order, rerun job `96810154525` completed the analytical pipeline and semantic gate against the exact one-day Chainlink source:

- Spot: 96,253 rows, aggregate trade IDs `849802848..849899100`, lag 250 ms;
- USD-M Perp: 114,281 rows, aggregate trade IDs `858791160..858905440`, lag 250 ms;
- candidate rounds: 284;
- research feature rows: 268;
- `skipped_no_aligned_market_data`: 14;
- four purged/embargoed OOS folds;
- 159 direction signals and scored outcomes;
- 230 independent pool projections.

`evidence/recent-economic-smoke-2026-08-18-to-19-last-success.json` preserves these exact source IDs, lags, row counts, hashes, and semantic counts.

## Why it matters

Timestamp-only deduplication can either reject valid high-frequency market data or, worse, select an arbitrary price. Source-native ordering preserves point-in-time semantics without converting equal timestamps into false duplicates.

## Applicability

- millisecond market data with multiple events per timestamp;
- as-of joins and feature alignment;
- Chainlink log alignment;
- any source where availability timestamp is coarser than event order.

## Exceptions / Limitations

The source-native key must actually be monotonic within the source contract. If a source does not expose such a key, the ambiguity must be modeled explicitly rather than resolved by ingestion order. Duplicate rows with the same exact source identity but conflicting payloads remain a data-integrity failure.

## Related files

- `src/pancake_prediction/alignment.py`
- `src/pancake_prediction/clickhouse_dataset.py`
- `src/pancake_prediction/binance_archive.py`
- `evidence/recent-economic-smoke-2026-08-18-to-19-last-success.json`

## Related cases

- `../cases/2026-08-20-v0-7-research-readiness/`
