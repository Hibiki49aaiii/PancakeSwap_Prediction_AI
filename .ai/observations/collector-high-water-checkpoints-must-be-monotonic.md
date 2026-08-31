# Collector high-water checkpoints must be monotonic

Status: observation / high
Date: 2026-08-29

Canonical collection progress is a high-water mark, not a mutable cursor that should move backward during normal operation.

## Rule

For persisted integer collector progress such as:

- `collector.progress.<digest>`;
- `<market>.last_collected_block`;

updates must be transactional and monotonic.

A delayed writer that completed only through block 100 must never overwrite a checkpoint already advanced to block 120.

## Reorg handling belongs in the read window

Do not lower the persisted high-water mark to handle reorgs.

The collector already resumes with a deliberate overlap:

`completed_through + 1 - reorg_lookback`

This separates two concerns cleanly:

- persisted checkpoint = furthest successfully completed height;
- reorg protection = how far backward the next read window intentionally replays.

## Concurrency

A read/compare followed by a separate generic metadata write is still racy.

The read, comparison and conditional write must happen under one SQLite write transaction such as `BEGIN IMMEDIATE`.

Generic string metadata should retain ordinary replacement semantics; only true integer high-water fields should use monotonic storage.

## Fail closed

If a field designated as monotonic integer progress already contains malformed or negative data, do not silently replace it. The provenance/state is inconsistent and should be surfaced.

## Revalidate against

- `src/pancake_prediction/store.py`
- `src/pancake_prediction/collector.py`
- Issue #15 tests
