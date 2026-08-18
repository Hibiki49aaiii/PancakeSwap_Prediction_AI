# Stage 1 — deterministic canonical replay

Stage 1 turns immutable Stage 0 evidence into a reproducible round-state artifact. It still does not train a model or make a profitability claim.

## ABI source anchor

The replay ABI is anchored to PancakeSwap's public V2 contract source:

- repository: `pancakeswap/pancake-smart-contracts`
- source: `projects/predictions/v2/contracts/PancakePredictionV2.sol`
- inspected source commit: `cb079908a30328e46d42fe8cc77b9f7d38a15c2f`

Relevant signatures are pinned as Keccak-256 topic hashes in `pancake_prediction.abi`:

- `StartRound(uint256)`
- `LockRound(uint256,uint256,int256)`
- `EndRound(uint256,uint256,int256)`
- `BetBull(address,uint256,uint256)`
- `BetBear(address,uint256,uint256)`
- `RewardsCalculated(uint256,uint256,uint256,uint256)`

The observed BetBull/BetBear topics share the same constants used by the execution-fork validation surface, so replay and execution cannot silently drift to different event identities.

## Frozen canonical snapshot

`freeze_canonical_snapshot` resolves exactly one canonical block hash for every height in the requested range and validates parent continuity before returning a snapshot hash.

The returned block-hash mapping is immutable. If the database later observes a reorg, replaying the old snapshot still selects the old fork's stored raw logs. A new snapshot receives a different `snapshot_hash`.

`raw_event_export_hash` binds the frozen snapshot hash and all selected raw log identities/data into a deterministic SHA-256 artifact. Logs from another fork or logs presented out of chain order fail closed.

## Decoder rules

Known Prediction events are decoded with exact topic counts and ABI word lengths. Indexed addresses must be correctly left-padded. Signed Chainlink prices decode as `int256`. A known event marked `removed=true` is rejected rather than treated as canonical evidence.

Unknown/admin event topics are not guessed; they are counted as ignored replay input.

## Replay invariants

Strict replay enforces:

1. one `StartRound` per epoch;
2. bets only after observed start and before lock;
3. one `LockRound`, after start;
4. one `EndRound`, after lock;
5. one `RewardsCalculated`, after end;
6. reward-base amount equals the replayed winning pool (or zero for an equal-price house win).

Partial-range forensic replay can explicitly disable the missing-start/missing-lock requirements, but it never relaxes duplicate events, bets-after-lock, malformed ABI, canonical-order, or removed-log checks.

## Outcome semantics

The V2 contract semantics are reproduced directly:

- `closePrice > lockPrice` -> Bull;
- `closePrice < lockPrice` -> Bear;
- `closePrice == lockPrice` -> house win.

## Stage 1 exit criteria

The code path is implemented, but Stage 1 evidence is not complete until a real canonical historical snapshot is replayed and the following are persisted:

- snapshot hash and raw-event export hash;
- recognized/ignored event counts;
- lifecycle violation count (must be zero for the validated full-range dataset, except explicitly documented invalid/refund rounds);
- per-epoch replayed pool totals reconciled against contract `rounds(epoch)` samples;
- source commit and collection manifest identifiers.
