# Stage 5B observed-fork evidence gate

Stage 5B is not complete merely because unit tests pass or because an execution ledger reports zero unresolved rows. It requires an observed local BSC fork campaign whose claim is bound to the exact code revision, exact bettable Prediction epoch, fork state, toolchain, and durable SQLite ledger that produced the result.

## Why this gate exists

The verifier rejects false-positive patterns including:

1. an empty execution database reporting zero unresolved intents;
2. an assumed or self-reported claim being relabeled as observed;
3. reusing a successful evidence JSON with another SQLite ledger;
4. reusing evidence from another Git revision, fork block/hash/timestamp, Anvil version, Prediction market, or epoch;
5. counting a final state that has no durable submitted attempt or FINALIZED transition;
6. combining a Bull transaction from one epoch with a Bear transaction from another;
7. claiming restart/drop/reorg success from scenario flags that are not supported by the same intent's durable transition and attempt history.

The gate therefore verifies the external claim and reconstructs the relevant execution facts independently from SQLite.

## Required observed results

A passing campaign must contain all of the following:

- at least one finalized `betBull(uint256)` transaction for the evidence epoch;
- at least one finalized `betBear(uint256)` transaction for that same epoch;
- each counted bet must target the exact PancakeSwap Prediction contract registered for the evidence market;
- each counted bet must carry a positive transaction value;
- each counted bet must have a durable submitted attempt with a transaction hash and a durable transition to `FINALIZED`;
- the fork block must be strictly inside the evidence round's betting window: `startTimestamp < forkBlockTimestamp < lockTimestamp`;
- zero unresolved durable execution intents;
- BSC source chain id 56;
- restart recovery observed and journaled on the same intent;
- dropped/replaced transaction recovery observed and journaled on the same intent and reserved nonce;
- reorg reconciliation observed and journaled on the same intent and reserved nonce;
- rejection of a non-loopback transaction RPC path observed and journaled.

Bull/Bear counts and scenario support are derived from the SQLite ledger. They are not accepted from JSON assertions alone.

## Evidence v3 binding

`EVIDENCE_VERSION = 3` binds all of the following fields into `claim_sha256`:

- evidence origin (`observed`, `assumed`, or `self_reported`);
- exact 40-character Git source SHA;
- timezone-aware observation timestamp;
- campaign id;
- Prediction market;
- exact Prediction epoch;
- round start timestamp;
- round lock timestamp;
- BSC source chain id;
- exact fork block number;
- exact fork block hash;
- exact fork block timestamp;
- pinned Anvil version string;
- SHA-256 of the completed SQLite execution ledger;
- all required adversarial scenario claims.

Changing any bound field invalidates `claim_sha256`. The verifier separately hashes the supplied SQLite database and rejects a mismatch, so a successful claim cannot be detached from the durable execution record that produced it.

## Durable scenario correlation

`execution_transitions` preserves state changes and `execution_observations` preserves scenario observations. The verifier correlates observation details to the exact `intent_id` and then checks that intent's attempts and transitions.

Required transition evidence includes:

- restart recovery: `SUBMITTING -> RETRYABLE`, with an `interrupted` submission attempt;
- dropped/replaced recovery: `SUBMITTED -> RETRYABLE`, followed by a distinct replacement transaction using the same reserved nonce;
- reorg recovery: `MINED -> REORGED -> RETRYABLE`, followed by a distinct replacement transaction using the same reserved nonce.

For drop/reorg, both original and replacement hashes must exist as submitted attempts for the same intent. A transition from some unrelated intent cannot satisfy another scenario.

## Fork-point discovery

`.github/workflows/stage5-fork-campaign.yml` no longer searches far back for a historical `StartRound` event. Instead, it starts from a confirmed safe head and scans only a short recent window. For each candidate block it reads, at that exact historical block tag:

- `currentEpoch()`;
- `rounds(currentEpoch)`;
- the candidate block timestamp.

The newest candidate satisfying `startTimestamp < block.timestamp < lockTimestamp` is selected. This minimizes dependence on old/pruned public RPC state and ensures the source fork block is already a confirmed, bettable Prediction state.

## Deterministic local-fork campaign

The dedicated workflow:

1. installs a pinned immutable Foundry nightly;
2. discovers the newest confirmed bettable BNBUSD fork point;
3. starts Anvil bound to `127.0.0.1` at that exact block;
4. confirms local chain id 56;
5. performs the fixed-block Prediction preflight and binds its epoch/start/lock snapshot to the evidence claim;
6. executes real local-fork Bull and Bear inclusion for the same epoch;
7. injects restart recovery;
8. injects a dropped transaction with `anvil_dropTransaction`, then proves same-nonce resubmission;
9. injects a snapshot-revert reorg, then proves `MINED -> REORGED -> RETRYABLE` and same-nonce resubmission;
10. proves a non-loopback transaction RPC is rejected;
11. writes the SQLite ledger and evidence-v3 JSON;
12. independently verifies the generated evidence against the workflow Git SHA;
13. uploads evidence plus the Anvil diagnostic log even when the campaign fails.

The upstream BSC endpoint is used as forked read-state input. Transaction-capable project code remains restricted to the loopback RPC adapter.

## Verification

```bash
python scripts/verify_stage5_fork_evidence.py \
  --db artifacts/stage5b-execution.sqlite3 \
  --evidence artifacts/stage5b-evidence.json \
  --source-sha "$GITHUB_SHA"
```

Exit code `0` means the observed Stage 5B evidence gate passed. Exit code `2` means one or more blockers remain.

## Current project status

The evidence-v3 schema, durable transition/observation journal, newest-bettable-state discovery, fault-injection runner, independent verifier, and GitHub Actions workflow are implemented. Those implementation facts do **not** constitute a Stage 5B pass.

Stage 5B remains blocked until an actual local-fork workflow run produces matching observed evidence and the independent verifier returns `ready=true` for the exact branch head.

No private key, mnemonic, mainnet signer, raw-transaction signer, or non-loopback project transaction broadcaster is introduced by this gate.
