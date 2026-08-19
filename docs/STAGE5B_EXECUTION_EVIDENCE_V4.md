# Stage 5B v4 — Source-Bound Verified Local BSC Fork Execution Evidence

## Purpose

Stage 5B v4 proves more than the existence of a local chain reporting BSC `chainId = 56`.
It binds one local development fork to independently observed BSC state, exercises the real PancakeSwap BNB Prediction contract on that exact local fork, and binds the resulting evidence to the byte-for-byte Python source implementation that generated it.

This stage is **not** mainnet execution evidence, funded-validation evidence, profitability evidence, or remote build attestation.

## Security boundary

The Stage 5B execution path has the following hard boundaries:

- local execution endpoint must be loopback (`127.0.0.1`, `localhost`, or `::1`);
- upstream BSC RPC uses a separate read-only RPC client;
- no private key is accepted by the readiness CLI;
- `eth_sendRawTransaction`, `eth_sign`, `personal_sign`, and signing RPCs are rejected before transport;
- local bets use deterministic impersonated EOAs and local-node `eth_sendTransaction` only;
- no mainnet transaction broadcast is performed;
- local mutation cycles are restored to the exact verified fork base with `evm_snapshot` / `evm_revert`.

The public local reset abstraction deliberately maps to snapshot restore rather than re-forking from upstream `latest`. This prevents an advancing BSC head from silently changing the block/hash under test.

## Protocol binding

The canonical Prediction target is:

`0x18B2A687610328590Bc8F2e5fEdDe3b582A49cdA`

The active Chainlink oracle is discovered from the canonical Prediction contract's `oracle()` through the upstream read-only BSC RPC at execution time.

A Stage 5B v4 artifact is only eligible for Stage 6A when its Prediction and Chainlink addresses match the current preflight protocol binding.

## Fork provenance requirements

Before any local Prediction mutation, v4 requires the provenance conditions represented by the diagnostic v2 probe:

1. local and upstream `eth_chainId` are both `56`;
2. the local fork-base block number is positive;
3. the local fork-base block hash equals the upstream BSC hash at the exact same block number;
4. canonical Prediction bytecode is present locally and equals upstream bytecode at that block;
5. active Chainlink oracle bytecode is present locally and equals upstream bytecode at that block;
6. local `evm_mine` advances the development fork;
7. exact local snapshot restore returns to the original fork-base block/hash;
8. Prediction and Chainlink bytecode still equal upstream after restore.

A local chain-id-56 development node without this upstream lineage cannot produce a Stage 6A-eligible pass.

## Real local Prediction execution requirements

From the verified fork base, the probe reads:

- `currentEpoch()`;
- `minBetAmount()`;
- `rounds(currentEpoch)`;
- `ledger(currentEpoch, testAccount)`.

The base block must be safely inside the current round's betting window before execution begins.

### BULL path

The probe must:

- fund a deterministic impersonated test EOA on the local node;
- submit local `betBull(epoch)` with a stake at or above `minBetAmount`;
- observe a successful local receipt;
- observe the expected `BetBull` event;
- verify BULL ledger position and amount;
- verify `totalAmount` and `bullAmount` increased by exactly the local stake;
- verify `bearAmount` remained unchanged;
- verify a second BULL bet from the same EOA/round reverts;
- revert the guarded EVM snapshot and prove exact baseline restoration.

### BEAR path

The probe must:

- verify a below-`minBetAmount` BEAR call reverts;
- fund a separate deterministic impersonated test EOA;
- submit local `betBear(epoch)`;
- observe a successful local receipt;
- observe the expected `BetBear` event;
- verify BEAR ledger position and amount;
- verify `totalAmount` and `bearAmount` increased by exactly the local stake;
- verify `bullAmount` remained unchanged;
- revert the guarded EVM snapshot and prove exact baseline restoration.

## Generator source binding

v4 adds `generator_source_fingerprint`.

The fingerprint contains:

- `algorithm = sha256`;
- a fixed manifest of Stage 5B trust-path Python source files and each file's SHA-256;
- `aggregate_sha256`, calculated over canonical JSON of that manifest.

The bound files are the installed implementations of:

- ABI encoding/decoding;
- Pancake protocol normalization;
- protocol binding discovery;
- upstream read-only RPC;
- loopback local-fork RPC;
- fork provenance probing;
- local Prediction execution probing;
- Stage 5B evidence construction;
- readiness CLI orchestration;
- Stage 6A evidence validation;
- the source-fingerprint implementation itself.

Stage 6A recomputes the same manifest from its current installed sources. A well-formed artifact with a different source manifest is rejected even if its outer evidence SHA-256 has been recomputed after tampering.

This property is a **same-code binding**. It does not prove who built or ran the code and must not be described as signed CI attestation or remote attestation.

## Evidence schema

Stage 6A accepts only:

`stage5b_verified_local_bsc_fork_execution_v4`

The payload contains:

- `generator_source_fingerprint`;
- `fork_provenance`;
- `prediction_execution`;
- `execution_transport = loopback_impersonated_eth_sendTransaction`;
- top-level safety declarations.

All of the following must be false both where applicable in the execution payload and at the top level:

- `private_key_used`;
- `raw_signed_transaction_used`;
- `mainnet_transaction_broadcast`.

The artifact SHA-256 covers the canonical payload. Stage 6A recomputes that hash, validates v4 schema semantics, validates current source equality, and rechecks protocol/runtime safety rather than trusting an `origin=observed, passed=true` label.

Provenance-only v2 evidence and execution v3 evidence are intentionally insufficient for Stage 6A.

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

Generate Stage 6A-eligible v4 execution evidence:

```bash
ppai-readiness stage5b-execute-fork \
  --local-rpc-url http://127.0.0.1:8545 \
  --upstream-rpc-url <READ_ONLY_BSC_RPC_URL> \
  --min-window-margin-seconds 10 \
  --output artifacts/stage5b-v4-evidence.json
```

`--chainlink-contract` is optional. If supplied, it must equal the address discovered from canonical Prediction `oracle()` at runtime.

## Verified real-fork checkpoint

GitHub Actions run `32272624509` produced a real Stage 5B v4 artifact with `passed=true`.

Observed checkpoint values:

- branch source head: `5dd1ad5aa18033a653d657973cf8d332f299d658`;
- BSC fork block: `116875488`;
- Prediction: `0x18b2a687610328590bc8f2e5fedde3b582a49cda`;
- active Chainlink oracle: `0x0567f2323251f0aab15c8dfb1967e4e8a7d42aee`;
- evidence payload SHA-256: `08909496e8f94375a52b9677fbe930a771276bf20e356b694b36d3ce0c58eedd`;
- generator source aggregate SHA-256: `10796e82881242a44790db76861b0c3eb31073cf3fc8b138c1dd1b243875d949`.

Independent artifact audit reserialized the canonical payload and source manifest and obtained the same hashes. BULL/BEAR receipt, event, ledger, pool-delta, revert, and exact-restoration checks were all true. Private-key, raw-signed-transaction, and mainnet-broadcast flags were all false.

## GitHub Actions workflow

`.github/workflows/stage5b-fork-evidence.yml` installs Foundry, discovers the live protocol binding from a read-only BSC RPC, starts Anvil on loopback, verifies provenance, executes BULL and BEAR paths, validates v4 source binding, and uploads evidence plus diagnostics.

The workflow retries with a fresh fork only when the current BSC round is too close to lock for a safe local execution test. Protocol/provenance/execution failures are not converted into retry passes.

After the verified v4 checkpoint was established, the expensive real-fork workflow was changed to manual-only `workflow_dispatch`.
