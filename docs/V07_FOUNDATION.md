# v0.7 canonical foundation

This is a clean-room re-bootstrap of the canonical repository. Legacy `PancakePredictionResearch` artifacts are evidence and design references, not the source tree.

## Implemented in this slice

1. BNB Chain (`chainId=56`) Prediction market registry for BNBUSD, BTCUSD, and ETHUSD.
2. Explicit `betBull(uint256)` and `betBear(uint256)` calldata encoding.
3. Deterministic unsigned semantic bet intent.
4. Read-oriented JSON-RPC client with no signing surface.
5. Loopback-only local-fork transaction client for future Anvil Stage 5B testing.
6. Unit tests for contract registry, ABI pins, semantic stability, validation, and loopback isolation.

## Evidence discipline

The repository distinguishes three classes of state:

- `implemented`: code and deterministic tests exist;
- `observed`: behavior was actually measured against the intended external environment;
- `economic evidence`: OOS/shadow/tiny-live measurements support a profitability claim.

No class may be promoted by inference from another class.

## Stage 0 extension

The branch contains the append-only raw block/log evidence store and block-pinned, reorg-aware historical collector described in `STAGE0_DATA_INTEGRITY.md`. Real BSC collection evidence is still pending and is not inferred from deterministic tests.

## Stage 1 extension

The branch now also freezes explicit canonical snapshots, emits deterministic snapshot/raw-event hashes, strictly decodes the core Prediction V2 event lifecycle, and reconstructs round state with lifecycle and reward-base invariants. See `STAGE1_REPLAY.md`.

## Next slice

Add persisted collection/data-quality manifests and real historical RPC validation, then reconcile replayed epochs against sampled on-chain `rounds(epoch)` state before any Stage 2 backtest rebuild.
