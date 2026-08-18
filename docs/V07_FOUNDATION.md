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

The branch now also contains the append-only raw block/log evidence store and block-pinned, reorg-aware historical collector described in `STAGE0_DATA_INTEGRITY.md`. Real BSC collection evidence is still pending and is not inferred from deterministic tests.

## Next slice

Implement deterministic Prediction V2 event decoding and replay over an explicitly selected canonical snapshot, followed by data-quality manifests and reproducible export hashes.
