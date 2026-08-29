# Issue #15 Implementation Plan

## Goal

Make canonical collector integer checkpoints transactional monotonic high-water marks so a slower concurrent collector cannot move persisted progress backwards.

## Existing gap

`EventStore.record_metadata()` is a generic string upsert. The collector currently uses it for integer progress keys:

- `collector.progress.<digest>`;
- `<market>.last_collected_block`.

Two collectors sharing one canonical DB can read the same earlier checkpoint and complete at different times. The later-finishing older-range writer can overwrite a newer checkpoint with a smaller integer.

## Reorg semantics

No checkpoint rollback is required for reorg handling.

`HistoricalCollector._resume_cursor()` replays from:

`completed_through + 1 - reorg_lookback`

Therefore the persisted checkpoint should remain the furthest completed high-water mark while the existing replay overlap handles recent reorgs.

## Design

### EventStore API

Add:

`record_monotonic_int_metadata(key: str, value: int) -> int`

Semantics:

1. reject negative values;
2. open SQLite connection;
3. `BEGIN IMMEDIATE`;
4. read existing metadata row;
5. if missing, insert requested value;
6. if present, parse as a non-negative base-10 integer;
7. malformed or negative stored value fails closed;
8. write only when requested value is greater;
9. equal/lower writes leave stored value untouched;
10. commit and return the authoritative stored integer.

Generic `record_metadata()` remains unchanged.

### Collector migration

Use the monotonic API for:

- per-address collector progress after each completed chunk;
- final `<market>.last_collected_block`.

No event/block collection logic changes.

## Why transaction rather than read-then-generic-upsert

A Python read followed by a separate generic write still has a race window. The compare and write must occur under one SQLite write transaction.

## Tests

- EventStore missing / advance / equal / lower;
- negative requested value;
- malformed existing value;
- negative existing value;
- collector progress cannot regress when a lower completion is persisted later;
- market last-collected cannot regress;
- resume still subtracts configured reorg overlap from the stored high-water mark.

## Safety

No schema change, signer, transaction authority, mainnet broadcast or funded execution.

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
- base SHA: `3edb2e85b31c9bd1812c2a5cdac9256aafcc2fe7`
- 455 tests / 87% / CI #1292 green.


# Implementation Result — 2026-08-29

Issue #15 implementation is complete.

## Implemented

- Added `EventStore.record_monotonic_int_metadata(key, value) -> int`.
- Requested values must be non-negative.
- Existing monotonic values must parse as non-negative base-10 integers or the update fails closed.
- Read / compare / conditional write runs under one SQLite `BEGIN IMMEDIATE` transaction.
- Missing key inserts the requested value.
- Greater value advances the high-water mark.
- Equal/lower values are idempotent and preserve the stored high-water mark.
- The method returns the authoritative stored integer after the transaction.
- Generic `record_metadata()` remains unchanged for non-high-water metadata.
- Historical collector per-address `collector.progress.*` writes now use the monotonic API.
- `<market>.last_collected_block` now uses the monotonic API.
- Existing reorg semantics remain unchanged: resume replays the configured overlap before the high-water mark rather than lowering the persisted checkpoint.
- Regression tests cover:
  - missing / advance / equal / lower writes;
  - negative requested values;
  - malformed and negative existing values;
  - a delayed lower chunk completion after a higher writer;
  - a delayed lower market completion after a higher `last_collected_block`;
  - existing reorg-lookback resume behavior.

## Verification

Production/test source SHA:
`b810ca8ca740cbaadd6832f987ef1cfc4f4b9e89`

Quality Evidence #290 / run `33245922051`:

- pytest: **461 passed**
- coverage: **87%**
- Ruff: success
- mypy strict: success
- Bandit: success
- pip-audit: success
- final quality gate: success

## Post-Implementation Review

### Correctness

Canonical progress cannot regress because of delayed concurrent writers. The reorg overlap remains a read-window concern, so there is no valid path that requires these high-water fields to decrease.

### Architecture

Only integer progress metadata received monotonic semantics. Oracle proofs, anchors and other generic metadata retain their existing replacement behavior.

### Concurrency

The comparison and write are performed inside one SQLite write transaction rather than through a racy read followed by a generic upsert.

### Compatibility

No schema migration is required and event/block collection semantics remain unchanged.

### Safety

No signer, private key, mainnet transaction signing, broadcast, funded execution, credential changes, profitability promotion or full-history promotion was introduced.

### Remaining concurrency boundary

Shared ClickHouse prospective Binance lineage still needs an independent concurrency review because `ReplacingMergeTree(ingest_version)` resolves duplicate trade IDs by the latest ingest version. That is intentionally out of scope for Issue #15.
