# Stage 0 — historical data integrity

Stage 0 establishes the evidence substrate. It does not model or predict anything.

## Storage model

Raw observations are append-only:

- `blocks`: immutable `(chain_id, block_hash)` header observations;
- `raw_logs`: immutable log identity keyed by `(chain_id, block_hash, tx_hash, log_index)`;
- `canonical_assignments`: append-only canonical-state changes recording which block hash became canonical at a height;
- `reorg_observations`: explicit old-hash -> new-hash changes;
- `ingestion_checkpoints`: operational state only, intentionally separate from evidence.

A reorg never deletes or rewrites the old fork. The new canonical hash is appended and both block/log histories remain auditable.

## Snapshot consistency

For each collection chunk the collector:

1. reads every block header in the range;
2. verifies parent-hash continuity;
3. checks the first block still descends from the prior stored canonical block when one exists;
4. fetches contract logs for the exact numeric range;
5. requires every log's `blockHash` to match the header captured for that height;
6. re-reads the chunk-end block and requires the hash to be unchanged;
7. only then commits the block/log evidence transactionally;
8. advances the operational checkpoint only after evidence commit.

A mismatch fails closed as `SnapshotChangedError`. A reorg that extends before the configured overlap boundary fails as `ReorgBeyondLookbackError` rather than silently stitching incompatible chains.

## Incremental collection

Incremental collection deliberately overlaps the prior checkpoint by `reorg_lookback_blocks` (default 64) and avoids the unstable chain tip using `safe_depth` (default 12). Both are explicit configuration values and must be reported with later experiments.

## Stage 0 exit criteria

Stage 0 is not complete merely because tables exist. Before promotion to Stage 1 we still need:

- a real historical-capable BSC RPC run;
- coverage/continuity metrics over the selected historical interval;
- duplicate/missing-log reconciliation against at least one independent source or RPC;
- persisted collection manifest (RPC identity, range, timestamps, config, code commit);
- deterministic raw-event export hash from a real frozen canonical snapshot (the hashing implementation now exists).
