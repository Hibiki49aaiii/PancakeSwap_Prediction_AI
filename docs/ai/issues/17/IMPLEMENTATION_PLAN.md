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


# Implementation Result — 2026-08-29

Issue #17 implementation is complete.

## Implemented

- Added `ClickHouseArchiveTarget` Protocol requiring:
  - parameterized ClickHouse reads;
  - JSON row inserts.
- `ingest_binance_archive()` now:
  1. validates input parameters;
  2. verifies the official archive checksum;
  3. resolves the target symbol/lineage;
  4. queries the same lineage for `source_name=binance-rest:<venue>`;
  5. requires exactly one non-negative integer `live_row_count`;
  6. rejects archive ingest when that count is greater than zero;
  7. only then streams archive rows into ClickHouse.
- Checksum failure therefore occurs before any live-lineage guard query or archive insert.
- Missing, duplicate, non-integer, negative or otherwise malformed live-row count results fail closed.
- `pcs-clickhouse binance-ingest` now acquires the exact same `BinanceLiveLineageProcessLock` used by:
  - `pcs-clickhouse binance-live-sync`;
  - `pcs-shadow-runtime`.
- Archive/live contention therefore fails before `ingest_binance_archive()` executes.
- Historical archive ingest remains supported while no prospective live row exists.
- No ClickHouse schema migration or campaign semantic change was introduced.

## Files Changed

- `.ai/observations/prospective-observation-lineages-need-single-writer-coordination.md`
- `docs/STAGE4_SHADOW.md`
- `docs/ai/issues/17/HUMAN_UNDERSTANDING.md`
- `docs/ai/issues/17/IMPLEMENTATION_PLAN.md`
- `src/pancake_prediction/clickhouse.py`
- `src/pancake_prediction/clickhouse_cli.py`
- `tests/test_clickhouse.py`
- `tests/test_clickhouse_cli.py`

## Verification

Production/test source SHA:
`76d381716b6554f740f5faceabbbc451271e45b3`

Quality Evidence #308 / run `33253722930`:

- pytest: **476 passed**
- coverage: **87%**
- Ruff: success
- mypy strict: success
- Bandit: success
- pip-audit: success
- final quality gate: success

Full PR CI #1336 / run `33253725278`:

- pytest: **476 passed in 23.46s**
- coverage: **87%**
- test: success
- ClickHouse integration: success
- Gitleaks: success
- pinned legacy **144,000-round** audit: success
- overall CI: success

## Post-Implementation Review

### Information-availability correctness

A historical archive row can no longer replace an already-prospective live row through a newer `ingest_version` on official ingest paths.

### Race closure

The presence guard alone would still race a concurrent live writer. Sharing the exact Issue #16 lineage lock between archive and live entrypoints makes the check-and-ingest sequence mutually exclusive on one host.

### Fail-closed behavior

Checksum mismatch fails before ClickHouse guard IO. Guard query shape/count corruption fails before archive insertion.

### Compatibility

Campaign bootstrap can still ingest historical archives before prospective collection starts. Once live provenance exists, that lineage is intentionally archive-frozen under the current replacement-key model.

### Safety

No signer, private key, mainnet transaction signing, broadcast, funded execution, credential issuance/change, profitability promotion or full-history promotion was introduced.

### Remaining boundary

Multi-host coordination and a future schema that safely separates historical/prospective observations remain out of scope.
