# Stage 5B observed-fork evidence gate

Stage 5B is not considered complete because unit tests pass or because the execution ledger has zero unresolved rows. It requires an observed local BSC fork campaign whose evidence is bound to the exact code revision and exact SQLite ledger that produced the result.

## Why this gate exists

Two false-positive patterns are explicitly rejected:

1. an empty execution database reporting zero unresolved intents;
2. an assumed or self-reported claim being relabeled as observed without changing its payload hash.

The Stage 5B verifier therefore checks both the durable execution ledger and a tamper-evident campaign claim.

## Required observed results

A passing campaign must contain all of the following:

- at least one finalized `betBull(uint256)` transaction;
- at least one finalized `betBear(uint256)` transaction;
- each counted transaction must target a registered PancakeSwap Prediction contract;
- each counted transaction must carry a positive transaction value;
- zero unresolved durable execution intents;
- BSC source chain id 56;
- restart recovery observed;
- dropped or replaced transaction recovery observed;
- reorg reconciliation observed;
- rejection of a non-loopback transaction RPC path observed.

The final Bull/Bear counts are derived from the SQLite ledger. They are not accepted from the JSON claim itself.

## Evidence binding

The JSON evidence claim binds:

- evidence version;
- origin (`observed`, `assumed`, or `self_reported`);
- exact Git source SHA;
- timezone-aware observation timestamp;
- campaign id;
- BSC source chain id;
- fork block number;
- SHA-256 of the completed SQLite execution ledger;
- all required adversarial scenario results.

`claim_sha256` is calculated over all of those fields. Changing `origin`, source SHA, scenario results, block number, or any other claim metadata invalidates the digest.

The verifier separately hashes the supplied SQLite database and rejects a mismatch. This prevents a successful evidence JSON from being reused with a different execution ledger.

## Verification

```bash
python scripts/verify_stage5_fork_evidence.py \
  --db artifacts/fork-execution.sqlite3 \
  --evidence artifacts/stage5b-evidence.json \
  --source-sha "$GITHUB_SHA"
```

Exit code `0` means the observed Stage 5B evidence gate passed. Exit code `2` means one or more blockers remain.

## Current project status

The verifier and schema are implemented, but this does **not** itself produce observed Stage 5B evidence. Stage 5B remains blocked until an actual local BSC fork campaign generates a matching SQLite ledger and observed evidence claim.

No private key, mnemonic, mainnet signer, or non-loopback transaction broadcaster is introduced by this gate.
