# Stage 5B v3 — Verified Local BSC Fork Execution Evidence

## Purpose

Stage 5B v3 proves more than the existence of a local chain reporting BSC `chainId = 56`.
It binds one local development fork to an independently observed BSC block and then exercises the real PancakeSwap BNB Prediction contract on that exact local fork.

This stage is **not** mainnet execution evidence, funded-validation evidence, or profitability evidence.

## Security boundary

The Stage 5B execution path has the following hard boundaries:

- local execution endpoint must be loopback (`127.0.0.1`, `localhost`, or `::1`);
- upstream BSC RPC uses the separate read-only RPC client;
- no private key is accepted by the readiness CLI;
- `eth_sendRawTransaction`, `eth_sign`, `personal_sign`, and signing RPCs are rejected;
- local bets use deterministic impersonated EOAs and local-node `eth_sendTransaction` only;
- no mainnet transaction broadcast is performed;
- all mutations are reverted to the exact verified fork base with `evm_snapshot` / `evm_revert`.

`evm_snapshot` / `evm_revert` are used instead of re-forking from `latest`. This is important because the upstream BSC head can advance during a test. A new fork against `latest` would not prove restoration to the same block/hash that was originally verified.

## Protocol binding

The canonical Prediction target is:

`0x18B2A687610328590Bc8F2e5fEdDe3b582A49cdA`

The active Chainlink oracle is not maintained as an unrelated hard-coded address. `ppai-readiness discover-binding` reads `oracle()` from the canonical Prediction contract over an upstream read-only BSC RPC at execution time.

A Stage 5B v3 artifact is only eligible for Stage 6A if its Prediction and Chainlink addresses match the current preflight binding.

## Fork provenance requirements

Before any local Prediction mutation, v3 requires the v2 provenance conditions:

1. local and upstream `eth_chainId` are both `56`;
2. the local fork-base block number is positive;
3. the local fork-base block hash equals the upstream BSC hash for the exact same block number;
4. canonical Prediction bytecode is present locally and equals upstream bytecode at that block;
5. active Chainlink oracle bytecode is present locally and equals upstream bytecode at that block;
6. local `evm_mine` advances the development fork;
7. exact local snapshot restore returns to the original fork-base block/hash;
8. Prediction and Chainlink bytecode still equal upstream after restore.

A local chain-id-56 node without this upstream lineage cannot produce a Stage 6A-eligible pass.

## Real local Prediction execution requirements

From the verified fork base, the probe reads:

- `currentEpoch()`;
- `minBetAmount()`;
- `rounds(currentEpoch)`;
- `ledger(currentEpoch, testAccount)`.

The base block must be safely inside the current round's betting window before execution begins.

The probe then exercises these paths against the real forked contract:

### BULL path

- fund a deterministic impersonated test EOA on the local node;
- submit local `betBull(epoch)` with a stake at or above `minBetAmount`;
- require a successful local receipt;
- require the expected `BetBull` event;
- require the BULL ledger position and amount to match;
- require `totalAmount` and `bullAmount` to increase by exactly the local stake;
- require `bearAmount` to remain unchanged;
- require a second BULL bet from the same EOA/round to revert;
- revert the EVM snapshot and prove exact baseline restoration.

### BEAR path

- require a below-`minBetAmount` BEAR call to revert;
- fund a separate deterministic impersonated test EOA;
- submit local `betBear(epoch)`;
- require a successful local receipt;
- require the expected `BetBear` event;
- require the BEAR ledger position and amount to match;
- require `totalAmount` and `bearAmount` to increase by exactly the local stake;
- require `bullAmount` to remain unchanged;
- revert the EVM snapshot and prove exact baseline restoration.

## Evidence schema

Stage 6A accepts only:

`stage5b_verified_local_bsc_fork_execution_v3`

The artifact contains two independently revalidated sections:

- `fork_provenance`
- `prediction_execution`

The top-level artifact also fixes the execution transport to:

`loopback_impersonated_eth_sendTransaction`

and requires all of the following to be false:

- `private_key_used`
- `raw_signed_transaction_used`
- `mainnet_transaction_broadcast`

The artifact SHA-256 covers the canonical payload. Stage 6A recomputes it and rechecks the schema rather than trusting an `origin=observed, passed=true` label.

## CLI

Discover the current canonical binding:

```bash
ppai-readiness discover-binding \
  --upstream-rpc-url <READ_ONLY_BSC_RPC_URL>
```

Generate provenance-only v2 diagnostic evidence:

```bash
ppai-readiness stage5b-verify-fork \
  --local-rpc-url http://127.0.0.1:8545 \
  --upstream-rpc-url <READ_ONLY_BSC_RPC_URL> \
  --output artifacts/stage5b-v2-provenance.json
```

Generate Stage 6A-eligible v3 execution evidence:

```bash
ppai-readiness stage5b-execute-fork \
  --local-rpc-url http://127.0.0.1:8545 \
  --upstream-rpc-url <READ_ONLY_BSC_RPC_URL> \
  --min-window-margin-seconds 10 \
  --output artifacts/stage5b-v3-evidence.json
```

`--chainlink-contract` is optional. If provided, it must equal the address returned by the canonical Prediction contract's `oracle()` at runtime.

## GitHub Actions evidence run

`.github/workflows/stage5b-fork-evidence.yml` installs the official Foundry toolchain, discovers the live protocol binding from a read-only BSC RPC, starts Anvil on loopback, verifies fork provenance, and runs the v3 local execution probe.

The workflow retries with a fresh fork only when the current BSC round is too close to lock for a safe execution test. A protocol/provenance/execution failure is not converted into a retry pass.

Successful evidence and diagnostic artifacts are kept separately so a Stage 5B result can be independently inspected.
