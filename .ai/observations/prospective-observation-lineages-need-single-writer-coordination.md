# Prospective observation lineages need single-writer coordination

Status: observation / high
Date: 2026-08-29

Prospective market-data rows are not safely commutative when the stored availability timestamp depends on the actual time a process observed the provider response.

## The failure mode

The Binance live collector stores an availability lower bound derived from the real HTTP response observation time.

The ClickHouse table deduplicates by one trade identity and keeps the row with the winning `ingest_version`.

If two writers concurrently:

1. read the same latest cursor;
2. fetch the same next trade IDs at different wall-clock times;
3. insert both versions;

the surviving prospective availability can depend on replacement order/version rather than one controlled observation history.

Retry-safe deduplication is therefore not the same as concurrency-safe prospective observation.

## Rule

Official prospective writers for one identical source lineage should be single-writer before the external fetch begins.

The lock identity must include every field that separates stored lineages, including:

- normalized data-store endpoint and database;
- market/symbol;
- venue;
- timestamp unit;
- availability lag.

Do not include credentials.

## Privacy

Hash the canonical lineage identity and use only the digest in local lock filenames. Do not expose raw endpoints, usernames, passwords, tokens or credentials in coordination paths.

## Semantic boundary

The lock is operational coordination, not prediction semantics.

Do not bind lock path/file/PID/current ownership into:

- campaign manifest digest;
- campaign Evidence;
- profitability or historical-completeness gates.

The underlying lineage configuration remains semantic; the mechanism used to serialize local writers does not.

## Scope boundary

A local SQLite/file lock protects writers on one host only.

Multi-host writers require either:

- a distributed lease; or
- a storage/data model where duplicate prospective observations combine deterministically without changing information availability semantics.

## Revalidate against

- `src/pancake_prediction/binance_live_lock.py`
- `src/pancake_prediction/process_lock.py`
- `src/pancake_prediction/shadow_runtime_cli.py`
- `src/pancake_prediction/clickhouse_cli.py`
- Issue #16 tests
