# PancakeSwap Prediction AI

Clean-room research and execution-readiness project for PancakeSwap Prediction on BNB Smart Chain.

## Canonical repository

This repository is the canonical source of truth for all work from v0.7 onward. Previous local artifacts named `PancakePredictionResearch` are legacy snapshots only.

## Objective

Estimate the calibrated PancakeSwap Prediction settlement distribution using only information available before the decision cutoff, then evaluate economics after realistic protocol and execution effects.

Primary target:

`P(BULL), P(BEAR), P(TIE | information available at decision time)`

`TIE` is a real protocol outcome: `closePrice == lockPrice` is a house-win round, not BEAR. Therefore `P(BEAR) = 1 - P(BULL)` is not a valid general assumption.

Economic evaluation includes treasury fee, gas, own-bet payout dilution, post-decision pool movement, execution probability/latency, reconciliation uncertainty, and house-win tie probability. Prediction accuracy alone is not a profitability criterion.

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
            -> evidence gates -> fork -> tiny-live readiness
```

## Validation stages

| Stage | Purpose | Current status |
|---|---|---|
| 0 | Data integrity | **Observed/Reconstructed stores, hash chain, transactional batches implemented** |
| 1 | Deterministic replay | **Observation-time replay and leakage cutoff implemented** |
| 2 | Cost-aware evaluation | **3-outcome EV, metrics, feature family v0.1, baseline model implemented** |
| 3 | Purged OOS | **Label-availability-safe folds, calibration split and train-only TIE prior implemented** |
| 4 | Paper / Shadow | **Observed collection -> hash-tip-bound promoted-model inference and durable tie-aware ledger implemented; real multi-round economics evidence still required** |
| 5A | Durable execution fault model | **Canonical implementation complete and CI tested** |
| 5B | BSC fork execution | Harness implemented; **BLOCKED until actual local-fork evidence is recorded** |
| 6A | Tiny-live safety preflight | Evidence gate implemented; assumed evidence cannot clear it |
| 6B | Funded validation | Not implemented |
| 7 | Production | Not reached |

Current code checkpoint before this documentation-only commit: **194/194 tests passed** on GitHub Actions for head `aa41196368fba40f513a01af2f7b41d80c58d78e`.

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
4. write the shadow decision against the exact hash-chain tip used for inference;
5. verify the Event Store hash chain before returning.

A reconstructed/historical store is rejected. The cycle does not contain wallet, signer, or transaction-broadcast functionality.

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

`historical_binance.py` can page public Binance aggregate trades into a reconstructed store. It validates aggregate-trade continuity and commits each validated page atomically. A sequence gap leaves the suspect page uncommitted.

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

## Shadow and evidence gates

The shadow ledger stores decision-time state separately from later settlement state, prevents duplicate decision/settlement records, requires settlement after the decision cutoff, preserves TIE as a distinct house-win outcome, and computes simulated economics from later observed final pools.

Stage 6A can only become ready when:

- Stage 5A evidence is an observed pass
- Stage 5B is an observed pass from an actual local BSC fork
- shadow economics evidence is an observed pass
- kill switch, wallet binding, per-round cap and balance cap pass
- there are no unresolved execution intents
- the decision window remains open
- signing and mainnet broadcasting remain disabled during preflight

`assumed` and `self_reported` evidence can never clear the gate.

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

# Historical Binance reconstruction with an explicit availability-latency assumption
ppai-collector --store data/historical.sqlite historical-binance \
  --dataset-id bnb-history-v1 \
  --start-time-ms <START_MS> \
  --end-time-ms <END_MS> \
  --assumed-latency-ns <LATENCY_NS>

# Verify stores
ppai-collector --store data/observed.sqlite verify-store
ppai-collector --store data/historical.sqlite verify-store --mode reconstructed
```

## Security boundary

Research/model layers do not hold private keys or signing authority. AI/LLM components may assist with research, feature analysis, evaluation and explanation; they are not wallet controllers.

## Evidence status

Infrastructure readiness is not evidence of profitability. Real historical/OOS performance, real observed multi-round shadow economics, and actual local-fork Stage 5B evidence remain separate requirements. Funded Stage 6B validation is not implemented.

## Next development phase

Phase 4.3 is evidence production rather than more infrastructure scaffolding:

1. collect real observed Binance + pinned BSC streams;
2. build a reconstructed historical dataset with explicit availability assumptions;
3. generate hash-bound dataset and promoted-model artifacts;
4. run baseline three-outcome OOS evaluation;
5. accumulate multi-round observed shadow economics;
6. keep Stage 5B blocked until an actual local BSC fork produces observed evidence.

## Development rule

All future work belongs in `Hibiki49aaiii/PancakeSwap_Prediction_AI` using branches and reviewable commits. Do not resume development in the former standalone `PancakePredictionResearch` repository name.
