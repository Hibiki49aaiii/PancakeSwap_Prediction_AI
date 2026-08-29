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

## Historical backfill boundary

Single-writer coordination is not sufficient if a historical archive writer is allowed to mutate the same prospective lineage later.

When historical archive rows and live-observed rows share one replacement key, a later archive ingest can replace the live row with an earlier reconstructed availability timestamp.

Under that table model:

- prepare archive history before prospective observation begins;
- once any prospective live provenance exists for a lineage, freeze that lineage against archive ingestion;
- make the archive check fail closed on malformed count/query state;
- use the same lineage lock for archive and live official entrypoints so the presence check cannot race a concurrent live insert.

A future data model may relax this only if historical and prospective observations are separated or combine with deterministic information-availability semantics.

## Preflight and runtime must share the same integrity definition

If normal runtime already knows how to reject a source-integrity violation, read-only preflight should reuse that exact invariant whenever it can prove the same state without mutation.

For Binance prospective lineages:

- archive-only history before live collection is valid;
- once live provenance exists, the latest cursor must still be from the expected live source;
- malformed live coverage/cursor state fails closed;
- preflight should call the same coverage/cursor helpers as runtime instead of reimplementing a weaker approximation.

This prevents a green preflight from being followed immediately by a deterministic runtime source-integrity failure.

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
- Issue #17 tests
- Issue #18 tests
