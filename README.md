# PancakeSwap Prediction AI

Clean-room research and execution-readiness project for PancakeSwap Prediction on BNB Smart Chain.

## Canonical repository

This repository is the canonical source of truth for all work from v0.7 onward. Previous local artifacts named `PancakePredictionResearch` are legacy snapshots only.

## Objective

Estimate the calibrated PancakeSwap Prediction settlement distribution using only information available before the decision cutoff, then evaluate economics after realistic protocol and execution effects.

Primary target:

`P(BULL), P(BEAR), P(TIE | information available at decision time)`

`TIE` is a real protocol outcome: `closePrice == lockPrice` is a house-win round, not BEAR. Therefore `P(BEAR) = 1 - P(BULL)` is not a valid general assumption.

Economic evaluation includes treasury fee, gas, own-bet payout dilution, post-decision pool movement, execution probability/latency, reconciliation uncertainty, claim/refund gas, and house-win tie probability. Prediction accuracy alone is not a profitability criterion.

## Core architecture

```text
Binance public Spot data -----------+
                                    |
Pinned BSC / Pancake / Chainlink ---+--> Observed Event Store
                                    |      (hash chained, append only)
                                    |
Historical public sources ----------+--> Reconstructed Event Store
                                           (explicit availability assumptions)

Event Store -> deterministic replay -> data quality -> feature families
            -> BULL/BEAR/TIE model -> calibration -> cost/EV engine
            -> purged availability-safe OOS -> shadow ledger
            -> schema-bound evidence gates -> verified local fork -> tiny-live readiness
```

## Validation stages

| Stage | Purpose | Current status |
|---|---|---|
| 0 | Data integrity | **Observed/Reconstructed stores, hash chain, transactional batches implemented** |
| 1 | Deterministic replay | **Observation-time replay and leakage cutoff implemented** |
| 2 | Cost-aware evaluation | **3-outcome EV, metrics, feature family v0.1, baseline model implemented** |
| 3 | Purged OOS | **Label-availability-safe folds, calibration split and train-only TIE prior implemented** |
| 4 | Paper / Shadow | **Observed collection, promoted-model inference, EV decision, later settlement/reconcile/summary and hash-bound HYBRID evidence implemented; real multi-round evidence still required** |
| 5A | Durable execution fault model | **SQLite restart/nonce/UNKNOWN/finalization drill, runtime-bound schema evidence and CLI implemented; target-runtime evidence must be produced explicitly** |
| 5B | BSC fork execution | **Real upstream-verified BSC fork execution observed and passed with source-bound v4 evidence; BULL/BEAR/revert/restore paths verified** |
| 6A | Tiny-live safety preflight | **Schema/runtime/protocol/source-bound Stage5 + qualified HYBRID shadow gate implemented; blocked until remaining Stage5A/shadow/OOS evidence is produced** |
| 6B | Funded validation | Not implemented |
| 7 | Production | Not reached |

Latest verified unit checkpoint: **379/379 tests passed** on GitHub Actions for head `5dd1ad5aa18033a653d657973cf8d332f299d658`.

Verified real Stage 5B v4 checkpoint: GitHub Actions run `32272624509` produced `passed=true` source-bound local-fork evidence at BSC fork block `116875488`. The canonical payload SHA-256 is `08909496e8f94375a52b9677fbe930a771276bf20e356b694b36d3ce0c58eedd`; its Stage 5B generator-source aggregate SHA-256 is `10796e82881242a44790db76861b0c3eb31073cf3fc8b138c1dd1b243875d949`.

## Protocol normalization

The canonical adapter targets BNB Chain (`chainId = 56`) and PancakeSwap's BNB Prediction contract. Protocol-facing values are normalized before entering research/economic modules.

Important boundaries:

- Pancake Prediction `treasuryFee`: denominator `10,000` (`200 = 2%`, `1000 = 10%`)
- internal economics engine: parts-per-million (`ppm`)
- public `rounds(epoch)` is normalized as a typed 14-field round state
- `totalAmount == bullAmount + bearAmount` is enforced
- the active Chainlink feed is discovered from the Prediction contract's `oracle()` reference rather than maintained as an unrelated hard-coded feed

## Observed data path

### Binance

`binance_public_rest.py` and `binance_websocket.py` use public market-data-only endpoints. The collector has no API-key, wallet, signing, or account-data path.

Implemented safeguards:

- `aggTrade` exchange trade time and local observation time remain separate
- `bookTicker` does not receive an invented exchange timestamp
- aggregate-trade IDs provide continuity checks
- WebSocket duplicates/stale messages are ignored
- forward aggregate-trade gaps stop ingestion and may be repaired from public REST
- restart resumes from the latest aggregate-trade ID persisted in Event Store
- REST bookTicker's missing sequence ID remains explicitly unavailable

### Pancake + Chainlink

All related on-chain view calls are pinned to one concrete BSC block. A snapshot reads, at the same block tag:

- `currentEpoch()`
- `treasuryFee()`
- `oracle()`
- `rounds(currentEpoch)`
- Chainlink `decimals()`
- Chainlink `description()`
- Chainlink `latestRoundData()`

The three resulting canonical events (round, oracle reference, oracle price) share one conservative local observation timestamp sampled only after all RPC responses have arrived. They are committed atomically with `EventStore.append_many()` so a partial protocol snapshot cannot remain after a database failure.

The observed head watcher also records block anchors, gaps and reorg anomalies. Normal new-head snapshots sample the observation clock only once through the pinned snapshot collector; same/lower-height reorg audit events receive their own observation time without pretending replacement state was the originally observed state.

`ReadOnlyJsonRpcClient` is allowlist-based and rejects transaction-signing/broadcast or other mutating RPC methods before transport.

### One-cycle observed shadow inference

`observed_cycle.py` connects the existing read-only components into one auditable sequence:

1. collect public Binance observations;
2. persist one pinned Pancake + Chainlink snapshot;
3. run the promoted model against the resulting observed Event Store;
4. write the shadow model decision against the exact hash-chain tip used for inference;
5. optionally convert the model probabilities into a paper BULL/BEAR/ABSTAIN economic decision using decision-time pool state, gas, own-stake dilution, assumed post-decision flow and execution probability;
6. verify the Event Store hash chain before returning.

A reconstructed/historical store is rejected. The cycle does not contain wallet, signer, or transaction-broadcast functionality.

### Shadow economics and settlement

Paper economics are deliberately separated from live trading.

A shadow economic decision records its decision-time assumptions and references the exact model decision/hash tip it consumed. Later, after the Pancake round has settled, the reconciler observes the final round state and computes paper PnL using the contract-compatible reward arithmetic. Winning/refund outcomes can include an explicit claim/refund gas assumption; loss/TIE/ABSTAIN paths do not invent a claim operation that would not occur.

The settlement layer supports:

- one-round settlement;
- batch reconciliation of pending shadow decisions;
- cumulative PnL and drawdown summaries;
- unresolved-round tracking;
- explicit claim/refund gas completeness;
- SHA-256-bound shadow evidence artifacts with decision -> model -> settlement lineage.

The resulting artifact is classified as `hybrid_shadow_not_live`: market state and final settlement are observed, while execution/stake behavior remains simulated. It is not live-profit evidence.

## Historical / reconstructed data path

Historical availability is not allowed to masquerade as live observation.

The Event Store has a persisted availability mode:

- `observed`: actual live/shadow observations
- `reconstructed`: historical records whose availability time depends on an explicit replay assumption

The two modes cannot be mixed in one SQLite database. Reconstructed events include hash-bound provenance metadata containing:

- dataset ID
- assumed availability latency
- actual backfill capture time
- optional source-artifact SHA-256

Each reconstructed SQLite store is persistently bound to one dataset namespace. That persisted binding is authoritative across restarts, and a database trigger rejects later inserts from a different reconstruction dataset.

`historical_evidence_run.py` composes historical Pancake lifecycle reconstruction, sparse decision-window Binance backfill, decision-time on-chain snapshots and dataset production into a reproducible evidence run. Binance historical ingestion is idempotent across reruns while sequence-gap validation remains active.

## Feature Family v0.1

Features are generated only from a leakage-safe replay snapshot:

- Binance mid price
- best-book spread (bps)
- Chainlink price
- Binance ↔ Chainlink divergence (bps)
- oracle source age
- aggressor signed notional / flow ratio / trade count
- Pancake BULL share
- Pancake pool imbalance
- exact Pancake pool amount in wei (model input uses log-scaled size)
- time to lock

The Data Quality Gate can block stale book/oracle/round state, excessive spread, insufficient recent trades, inadequate lock margin, and non-monotonic source sequences.

## Baseline AI and OOS evaluation

The canonical baseline is a deterministic 3-class multinomial softmax model with feature scaling, L2 regularization, artifact SHA-256, and scalar temperature calibration.

Walk-forward evaluation enforces chronological separation:

`model-train -> calibration -> test`

Additional leakage guards:

- training labels must have become available by the earliest test decision cutoff
- purged boundaries protect adjacent windows
- test examples cannot appear in more than one OOS fold
- TIE is never silently collapsed into a binary model
- when a fold has no observed TIE, it requires an explicit 3-outcome prior
- optional Wilson TIE prior is derived **inside each fold from model-training outcomes only**, never from later calibration/test/full-dataset outcomes

Primary probability diagnostics include multiclass Brier score, multiclass log loss, top-label accuracy and calibration error. These are model-quality measurements, not evidence of economic profitability.

## Stage 5 execution-readiness evidence

### Stage 5A durability drill

`execution_drill.py` runs against a fresh local SQLite database and verifies, by actual close/reopen cycles:

- WAL journal mode and FULL synchronous durability policy;
- unresolved intent recovery after restart;
- duplicate active nonce rejection;
- persistence of UNKNOWN after a missing/non-canonical receipt;
- later transition to FINALIZED after the configured confirmation threshold;
- terminal-state nonce release;
- cleanup of the nonce-reuse intent;
- zero unresolved intents at drill completion.

The drill uses synthetic nonces and transaction hashes. It creates, signs and broadcasts no blockchain transaction.

Stage 5A schema `stage5a_execution_drill_v2` additionally embeds a canonical `execution_runtime_fingerprint_v1` covering Python implementation/version, OS/release/architecture, SQLite library/source ID and sorted SQLite compile options. Hostname, username, machine ID and filesystem path are intentionally excluded. Stage 6A recomputes the current runtime fingerprint and rejects Stage 5A evidence from a different runtime stack.

### Stage 5B verified and executed local BSC fork

A local node reporting `chainId=56` is not sufficient evidence of a BSC fork.

The provenance layer `probe_verified_local_bsc_fork` requires an independent read-only upstream BSC RPC and verifies:

- local and upstream chain IDs are 56;
- the local fork-base block hash equals the upstream BSC hash at the same block number;
- Prediction and Chainlink bytecode at the fork block match upstream;
- local `evm_mine` advances the development fork;
- the public reset abstraction restores the exact pre-mutation fork base using `evm_snapshot` / `evm_revert` rather than re-forking from moving upstream `latest`;
- post-restore block hash and bytecode still match upstream.

Provenance-only schema `stage5b_verified_local_bsc_fork_v2` records the exact Prediction contract and Chainlink oracle addresses used by the probe, but **cannot clear Stage 6A**.

The executed evidence schema is `stage5b_verified_local_bsc_fork_execution_v4`. It additionally requires real local-fork Prediction execution and records/verifies:

- canonical BNB Prediction target and active `oracle()` Chainlink binding;
- successful local `betBull(uint256)` and `betBear(uint256)` transactions from deterministic node-impersonated EOAs;
- mined receipts and expected Bet events;
- ledger position/amount and round-pool amount deltas;
- duplicate same-wallet bet rejection;
- below-`minBetAmount` rejection;
- exact state restoration after BULL and BEAR mutation cycles;
- `private_key_used=false`;
- `raw_signed_transaction_used=false`;
- `mainnet_transaction_broadcast=false`.

v4 also embeds `generator_source_fingerprint`, a canonical SHA-256 manifest of the installed Python source files that implement the Stage 5B trust path. Stage 6A recomputes the manifest from its own installed source and rejects evidence generated by a byte-for-byte different Stage 5B implementation. This is a same-code binding, not remote build/runner attestation.

The real v4 evidence produced by run `32272624509` passed at fork block `116875488` against Prediction `0x18b2a687610328590bc8f2e5fedde3b582a49cda` and active oracle `0x0567f2323251f0aab15c8dfb1967e4e8a7d42aee`. Its canonical payload and source-manifest hashes were independently recomputed and matched the recorded values.

`LocalForkJsonRpcClient` is loopback-only. It can submit `eth_sendTransaction` only to that local development node with node-impersonated accounts. It exposes no private-key input, rejects `eth_sendRawTransaction` and signing RPCs before transport, and uses a separate read-only client for upstream BSC.

The expensive real-fork workflow `.github/workflows/stage5b-fork-evidence.yml` is manual-only through `workflow_dispatch` after the v4 checkpoint was established.

## Stage 6A evidence gate

Stage 6A can only become ready when all three evidence families and runtime safety state independently pass.

Required evidence:

- Stage 5A: `origin=observed`, `passed=true`, correct `stage5a_execution_drill_v2` schema, all durability checks true, zero unresolved intents, no transaction/signing/broadcast activity, and an execution runtime fingerprint equal to the runtime evaluating preflight;
- Stage 5B: `origin=observed`, `passed=true`, correct `stage5b_verified_local_bsc_fork_execution_v4` schema, current generator-source fingerprint, upstream block-hash/bytecode provenance, exact protocol binding, successful BULL/BEAR local execution/revert/restore checks, and no private-key/raw-signed/mainnet-broadcast activity;
- Shadow economics: `origin=hybrid`, qualified `shadow_gate_evidence_v1`, minimum settled-round/PnL/expected-return criteria, drawdown bound, all represented decisions settled, and claim/refund gas fully modeled when required.

Runtime preflight additionally requires:

- canonical Prediction contract binding;
- a syntactically valid active Chainlink oracle binding matching Stage 5B evidence;
- kill switch armed;
- wallet binding OK;
- per-round cap OK;
- balance cap OK;
- zero unresolved execution intents;
- decision window open;
- signing disabled during preflight;
- mainnet broadcasting disabled during preflight.

The gate recomputes payload SHA-256 and revalidates the stage-specific schema. Generic `origin=observed, passed=true` JSON, `assumed`, `self_reported`, misclassified shadow evidence, evidence from another runtime stack, old Stage 5B v2/v3 evidence, source-manifest-tampered v4 evidence, and fork evidence for another protocol address cannot clear the gate.

`Evidence.from_json_bytes` also requires strict JSON top-level types. In particular, `passed` must be a real JSON boolean; truthy strings such as `"false"` cannot be coerced into a pass.

## Collector CLI

The installed command is `ppai-collector`. It intentionally exposes no wallet/private-key/transaction argument.

```bash
# One public Binance REST observation
ppai-collector --store data/observed.sqlite binance-rest-once

# Continuous Binance market-data-only WebSocket collection
ppai-collector --store data/observed.sqlite binance-ws

# One pinned Pancake + Chainlink snapshot (user-supplied read-only BSC RPC)
ppai-collector --store data/observed.sqlite protocol-once --rpc-url <BSC_RPC_URL>

# One complete signer-free observation -> promoted-model shadow inference cycle
ppai-collector --store data/observed.sqlite shadow-cycle-once \
  --rpc-url <BSC_RPC_URL> \
  --model-artifact <PROMOTED_MODEL_JSON>

# The same cycle with paper economics enabled
ppai-collector --store data/observed.sqlite shadow-cycle-once \
  --rpc-url <BSC_RPC_URL> \
  --model-artifact <PROMOTED_MODEL_JSON> \
  --shadow-stake-wei <STAKE_WEI> \
  --shadow-gas-cost-wei <BET_GAS_WEI> \
  --shadow-claim-or-refund-gas-cost-wei <CLAIM_OR_REFUND_GAS_WEI>

# Reconcile pending paper decisions after their Pancake rounds settle
ppai-collector --store data/observed.sqlite shadow-settle-pending --rpc-url <BSC_RPC_URL>

# Summarize accumulated paper economics
ppai-collector --store data/observed.sqlite shadow-summary

# Reproducible historical evidence run
ppai-collector --store data/historical.sqlite historical-evidence-run \
  --dataset-id bnb-history-v1 \
  --rpc-url <ARCHIVE_OR_HISTORICAL_BSC_RPC_URL> \
  --from-block <START_BLOCK> \
  --to-block <END_BLOCK> \
  --decision-lead-ns <DECISION_LEAD_NS> \
  --binance-latency-ns <BINANCE_AVAILABILITY_LATENCY_NS> \
  --onchain-latency-ns <ONCHAIN_AVAILABILITY_LATENCY_NS>

# Verify stores
ppai-collector --store data/observed.sqlite verify-store
ppai-collector --store data/historical.sqlite verify-store --mode reconstructed
```

## Artifact CLI

`ppai-artifact` freezes reconstructed datasets, evaluates OOS performance, trains promoted models, freezes shadow evidence, and derives a Stage 6A shadow gate artifact.

```bash
# Freeze the accumulated observed shadow ledger
ppai-artifact build-shadow-evidence \
  --store data/observed.sqlite \
  --output artifacts/shadow-evidence.json

# Evaluate the shadow artifact against an explicit acceptance policy
ppai-artifact build-shadow-gate-evidence \
  --shadow-evidence artifacts/shadow-evidence.json \
  --min-settled-rounds <N> \
  --min-conditional-net-pnl-wei <WEI> \
  --max-conditional-drawdown-wei <WEI> \
  --min-average-selected-expected-return <RETURN> \
  --output artifacts/shadow-gate.json
```

## Readiness CLI

`ppai-readiness` generates Stage 5 evidence without exposing a mainnet signer or broadcast path.

```bash
# Stage 5A: run the actual local SQLite restart/durability drill
ppai-readiness stage5a-drill \
  --database artifacts/stage5a-drill.sqlite3 \
  --required-confirmations 3 \
  --output artifacts/stage5a-evidence.json

# Stage 5B diagnostic: verify local-fork provenance only (v2, not Stage6A-eligible)
ppai-readiness stage5b-verify-fork \
  --local-rpc-url http://127.0.0.1:8545 \
  --upstream-rpc-url <READ_ONLY_BSC_RPC_URL> \
  --output artifacts/stage5b-provenance.json

# Stage 5B gate evidence: verify provenance and execute local BULL/BEAR paths (v4)
ppai-readiness stage5b-execute-fork \
  --local-rpc-url http://127.0.0.1:8545 \
  --upstream-rpc-url <READ_ONLY_BSC_RPC_URL> \
  --min-window-margin-seconds 10 \
  --output artifacts/stage5b-v4-evidence.json
```

The active Chainlink oracle is discovered from canonical Prediction `oracle()` when `--chainlink-contract` is omitted. A successful Stage 5B v4 artifact is local-fork execution evidence only; it is not funded-live or profitability evidence.

## Security boundary

Research/model layers do not hold private keys or signing authority. AI/LLM components may assist with research, feature analysis, evaluation and explanation; they are not wallet controllers.

The observed collector and upstream BSC RPC client are read-only. The local-fork execution client is restricted to loopback development nodes and deterministic node-impersonated accounts. It never accepts a private key or raw signed transaction, and it cannot submit local execution calls to the separate upstream client. Stage 6B funded validation remains a separate implementation and operational gate.

## Evidence status

Infrastructure readiness is not evidence of profitability.

Stage 5B fork execution evidence is now established. Still required before any Stage 6B funded-validation decision:

- real reconstructed historical/OOS metrics from the current three-outcome pipeline;
- real multi-round observed-market HYBRID shadow economics with all rounds settled and costs fully modeled;
- explicit Stage 5A v2 evidence generated on the runtime that will evaluate preflight;
- an actual Stage 6A preflight pass with signing and mainnet broadcasting still disabled.

## Next development phase

Phase 4.3 is now primarily evidence production:

1. collect a sustained observed Binance + pinned BSC stream;
2. execute a reconstructed historical evidence run with explicit availability assumptions;
3. generate the bound dataset/evaluation/promoted-model manifest chain;
4. establish baseline three-outcome OOS metrics;
5. accumulate, settle and freeze enough observed-market HYBRID shadow rounds to evaluate economics;
6. run `ppai-readiness stage5a-drill` on the intended validation runtime;
7. evaluate Stage 6A readiness only when the remaining evidence families pass;
8. keep Stage 6B funded validation as a separate explicit operational/legal gate.

## Development rule

All future work belongs in `Hibiki49aaiii/PancakeSwap_Prediction_AI` using branches and reviewable commits. Do not resume development in the former standalone `PancakePredictionResearch` repository name.
