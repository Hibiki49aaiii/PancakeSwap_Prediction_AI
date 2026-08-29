# Issue #17 Implementation Plan

## Goal

Prevent Binance archive ingestion from replacing prospectively observed live rows in an already-live ClickHouse lineage.

## Existing hazard

The table key excludes `source_name` and uses `ReplacingMergeTree(ingest_version)`.

For the same aggregate trade ID:

- live REST row availability is bounded by the actual HTTP observation time;
- archive row availability is derived from historical trade time plus configured lag.

A later archive ingest can therefore replace a live row with an earlier apparent availability time.

## Design

### 1. Archive ingest target Protocol

Require the archive ingest target to support both:

- parameterized read queries;
- JSON row insertion.

The production `ClickHouseHttpClient` already provides both.

### 2. Prospective-lineage guard

After archive checksum verification and before reading/inserting archive rows:

query the same lineage for physical rows with:

`source_name = binance-rest:<venue>`

Identity dimensions:

- venue;
- symbol;
- timestamp unit;
- availability lag.

The query deliberately checks for the existence of prospective live provenance before writing archive rows.

Requirements:

- exactly one count row;
- count must parse as non-negative integer;
- count > 0 -> reject archive ingest.

### 3. Official CLI locking

`pcs-clickhouse binance-ingest` acquires the same `BinanceLiveLineageProcessLock` introduced by Issue #16.

Ordering:

1. schema check;
2. lineage lock acquisition;
3. checksum + live-presence guard + archive ingest;
4. release.

This closes the race where archive checks before a live writer inserts, while both run concurrently on one host.

### 4. Existing live guard

The live collector's existing fail-closed detection of non-live lineage advancement remains as defense in depth.

## Compatibility

Historical archive preparation before prospective collection remains supported.

Once a lineage has prospective live rows, it becomes archive-frozen under the current table model.

## Tests

- no live rows -> archive success;
- live rows -> reject before insert;
- malformed/missing count response -> fail closed;
- invalid checksum -> no query;
- archive CLI holds lineage lock;
- existing live lock prevents archive ingest call;
- existing archive provenance tests remain valid.

## Safety

No schema migration, signing, broadcast or funded execution.

## Verification

- Ruff
- mypy strict
- pytest + coverage
- Bandit
- pip-audit
- Gitleaks
- ClickHouse integration
- pinned 144,000-round audit

## Base

- branch: `agent/v0.7-alpha-research`
- base SHA: `8ba478b50bbc78d83dbec161ed4c5b7e8c90b946`
- 469 tests / 87% / CI #1330 green.
