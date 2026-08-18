# Stage 5B observed-fork evidence gate

Stage 5B is not considered complete because unit tests pass or because the execution ledger has zero unresolved rows. It requires an observed local BSC fork campaign whose evidence is bound to the exact code revision, fork state, toolchain, market, and SQLite ledger that produced the result.

## Why this gate exists

False-positive patterns are explicitly rejected, including:

1. an empty execution database reporting zero unresolved intents;
2. an assumed or self-reported claim being relabeled as observed without invalidating its digest;
3. reusing a successful evidence JSON with a different SQLite execution ledger;
4. reusing evidence from another code revision, fork block, fork block hash, Anvil version, or Prediction market.

The Stage 5B verifier therefore checks both the durable execution ledger and a tamper-evident campaign claim.

## Required observed results

A passing campaign must contain all of the following:

- at least one finalized `betBull(uint256)` transaction;
- at least one finalized `betBear(uint256)` transaction;
- each counted transaction must target the exact PancakeSwap Prediction contract registered for the evidence market;
- each counted transaction must carry a positive transaction value;
- zero unresolved durable execution intents;
- BSC source chain id 56;
- restart recovery observed;
- dropped or replaced transaction recovery observed;
- reorg reconciliation observed;
- rejection of a non-loopback transaction RPC path observed.

The final Bull/Bear counts are derived from the SQLite ledger. They are not accepted from the JSON claim itself.

## Evidence v2 binding

`EVIDENCE_VERSION = 2` binds the following fields into `claim_sha256`:

- evidence origin (`observed`, `assumed`, or `self_reported`);
- exact 40-character Git source SHA;
- timezone-aware observation timestamp;
- campaign id;
- Prediction market;
- BSC source chain id;
- exact fork block number;
- exact fork block hash;
- pinned Anvil version string;
- SHA-256 of the completed SQLite execution ledger;
- all required adversarial scenario results.

Changing any of those fields invalidates `claim_sha256`. The verifier separately hashes the supplied SQLite database and rejects a mismatch, so a successful claim cannot be reused with another execution ledger.

## Deterministic fork campaign

`.github/workflows/stage5-fork-campaign.yml` performs the observed campaign on a loopback-only Anvil node. The workflow:

1. installs a pinned immutable Foundry nightly;
2. searches confirmed BSC history for the latest BNBUSD `StartRound` event;
3. forks immediately after that round start to avoid a moving betting-window race;
4. starts Anvil bound to `127.0.0.1` only;
5. performs real local-fork Bull/Bear inclusion;
6. injects restart, dropped-transaction, and snapshot-revert/reorg recovery scenarios;
7. proves a non-loopback transaction RPC is rejected;
8. writes the SQLite ledger and observed evidence JSON;
9. independently verifies the generated evidence against the workflow Git SHA.

The upstream BSC endpoint is used only by Anvil as the source of forked read state. Transaction-capable project code remains restricted to the loopback RPC adapter.

## Verification

```bash
python scripts/verify_stage5_fork_evidence.py \
  --db artifacts/stage5b-execution.sqlite3 \
  --evidence artifacts/stage5b-evidence.json \
  --source-sha "$GITHUB_SHA"
```

Exit code `0` means the observed Stage 5B evidence gate passed. Exit code `2` means one or more blockers remain.

## Current project status

The verifier, evidence-v2 schema, deterministic fork-block discovery, fault-injection runner, and GitHub Actions workflow are implemented. None of those implementation facts alone constitutes a Stage 5B pass: the gate remains blocked until an actual workflow/local-fork campaign generates matching observed evidence and the independent verifier returns ready.

No private key, mnemonic, mainnet signer, raw-transaction signer, or non-loopback project transaction broadcaster is introduced by this gate.