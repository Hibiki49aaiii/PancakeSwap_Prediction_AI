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
