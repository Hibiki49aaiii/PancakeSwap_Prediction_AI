# Stage 4 Paper / Shadow Validation

Stage 4 records decisions that would have been taken at the configured pre-lock cutoff without signing or broadcasting a transaction. Its purpose is to prove that the research pipeline can operate prospectively for a sustained period with auditable timing, model provenance, settlement reconciliation, and economic observations.

Stage 4 is not a profitability gate and is not a transaction-execution stage.

## Safety boundary

The active Stage 4 contract is:

- no private key or mnemonic;
- no wallet unlock;
- no raw transaction signing;
- no mainnet transaction broadcast;
- no funded stake;
- signing_enabled=false;
- live_broadcast=false;
- funded_execution=false.

The Stage 5 loopback/local-fork execution path remains separate.

## Decision path

    anchored BSC Prediction + active Chainlink history
                       |
    prospectively observed Binance Spot / Perp aggTrades
                       |
    pre-lock ResearchFeatureRow
                       |
    settled-history-only model fit
                       |
    past-only calibration
                       |
    past-only pool projection
                       |
    explicit fee/gas/latency EV
                       |
    ResearchPredictionRecord
                       |
    append-only Shadow Ledger
                       |
    later canonical settlement reconciliation

src/pancake_prediction/shadow_inference.py implements the single-target inference boundary.

For a target epoch it requires:

1. exactly one canonical target round;
2. exactly one target feature row at the canonical decision timestamp;
3. model training rows whose outcomes had already settled before the target decision;
4. an explicit purge boundary;
5. a calibration tail drawn only from eligible historical rows;
6. a pool projection whose own training boundary is also purged and settled before the target decision;
7. explicit stake, treasury fee, bet gas, claim gas, inclusion latency, and minimum-EV assumptions.

The target round's final label, final pool, close price, reward calculation, and any later settlement information are not inputs to the decision. Regression tests mutate the target final outcome and final pool and require the generated Shadow prediction, EV, and pool projection to remain unchanged.

## Prospective observation boundary

Historical archives are valid for training and historical validation when their provenance and timestamp assumptions are bound into the campaign.

Live Stage 4 decisions use a stricter rule: a Binance trade fetched from the public REST endpoint is not considered known until the runtime actually observes the HTTP response.

For live rows:

    available_at_ms = max(
        HTTP response observation time,
        trade timestamp + configured availability lag
    )

This deliberately prevents a runtime restart from backfilling earlier trades and pretending they were known before a decision cutoff.

Consequences:

- a new Stage 4 runtime needs at least the configured flow-lookback warmup before current market microstructure can be used prospectively;
- recently fetched historical trades may exist in ClickHouse but remain ineligible for a decision whose cutoff predates the actual fetch;
- a missing prospective target row is reported as target_not_ready, not silently reconstructed from future knowledge.

The live collector uses Binance aggregate-trade endpoints:

- Spot: /api/v3/aggTrades
- USD-M Futures: /fapi/v1/aggTrades

Pagination is trade-ID based after the initial bounded bootstrap window. The runtime fails closed if max_pages is exhausted before it catches up.

## Anchored BSC incremental collection

src/pancake_prediction/shadow_chain_sync.py extends an already bootstrapped canonical SQLite database.

The database must already contain:

- <market>.last_collected_block;
- <market>.oracle_proxy_anchor_address;
- <market>.oracle_anchor_address.

Before every incremental collection the runtime proves that the latest PancakeSwap Prediction oracle proxy and its Chainlink aggregator are still the same source route as the campaign anchor.

If either route changes, collection stops with a source-bound campaign error. The runtime does not automatically merge a new oracle route into the existing model lineage.

The collector overlaps the previous range by the configured reorg lookback, re-validates canonical blocks, and then collects:

- PancakeSwap Prediction events;
- AnswerUpdated events from the proven Chainlink aggregator.

The persisted integer collector checkpoints are monotonic high-water marks. Per-address `collector.progress.*` keys and `<market>.last_collected_block` are updated under one SQLite `BEGIN IMMEDIATE` read/compare/write transaction. A delayed collector that completed an older range cannot overwrite a newer stored height.

Reorg protection does not require checkpoint rollback. Resume still intentionally replays from approximately `completed_through + 1 - reorg_lookback`, so the persisted high-water remains monotonic while the read window overlaps recent canonical history.

The CLI entrypoint is:

    pcs-prediction shadow-chain-sync \
      --market BNBUSD \
      --db artifacts/bnbusd-history.sqlite

BSC_RPC_URL is read from the environment unless --rpc-url is supplied. The endpoint is never included in the JSON report.

## Prospective Binance live collection

src/pancake_prediction/binance_live.py writes live aggregate trades into the same retry-safe ClickHouse binance_agg_trades table used by the research pipeline.

Official prospective writers for one identical Binance ClickHouse lineage are local single-writer.

The coordination identity includes:

- normalized ClickHouse endpoint and database;
- market/symbol;
- venue;
- timestamp unit;
- availability lag.

The canonical identity is SHA-256 hashed and only the digest appears in the lock filename under the OS temporary directory. Credentials are excluded.

Both `pcs-shadow-runtime` and `pcs-clickhouse binance-live-sync` use this same lineage lock. Contention fails before the Binance HTTP fetch begins. Spot and Perp use separate lock identities.

This is necessary because retry-safe `ReplacingMergeTree(ingest_version)` deduplication alone is not enough for prospective observation: two concurrent fetches of the same trade ID can have different actual HTTP observation times, so replacement order could otherwise change the surviving availability timestamp.

The lineage lock is local process coordination only and is deliberately excluded from campaign manifest identity and campaign Evidence. A multi-host deployment would require a distributed coordination design or a different commutative observation model.

Historical archive preparation is also one-way under the current table model. `pcs-clickhouse binance-ingest` acquires the same lineage lock as live writers, verifies the archive checksum, then checks for existing `binance-rest:<venue>` provenance before any archive row insert. If prospective live rows already exist for that lineage, archive ingest fails closed.

This keeps a later archive `ingest_version` from replacing a live row whose `event_timestamp_ms` records a later actual HTTP observation time. Historical Binance archives must therefore be prepared before prospective Stage 4 collection begins for that exact lineage.

Example Spot sync:

    pcs-clickhouse binance-live-sync \
      --market BNBUSD \
      --venue spot \
      --timestamp-unit auto \
      --availability-lag-ms 250

Example USD-M Futures sync:

    pcs-clickhouse binance-live-sync \
      --market BNBUSD \
      --venue um_futures \
      --timestamp-unit milliseconds \
      --availability-lag-ms 250

timestamp-unit is the ClickHouse lineage key. REST timestamps themselves are parsed as milliseconds. It must match the lineage selected by the Stage 4 research dataset.

## Automatic target selection

select_shadow_target() identifies an eligible epoch only while:

    decision_timestamp <= now < scheduled_lock - inclusion_latency

The automatic path therefore has a finite submission-equivalent window even though Stage 4 never sends a transaction.

The runtime checks the wall clock a second time after feature construction and inference. If the latest-submission boundary has already been reached, the prediction is not appended and the cycle reports:

    missed_submission_deadline

This prevents a slow model run from being credited as a decision that could realistically have been made in time.

pcs-clickhouse shadow-infer supports two modes:

Explicit deterministic epoch:

    pcs-clickhouse shadow-infer \
      --market BNBUSD \
      --db artifacts/bnbusd-history.sqlite \
      --shadow-db artifacts/shadow.sqlite3 \
      --target-epoch <epoch> \
      --spot-availability-lag-ms 250 \
      --perp-availability-lag-ms 250 \
      --chainlink-availability-lag-ms 1000 \
      --max-chainlink-age-ms 300000 \
      --feature-lead-seconds 20 \
      --stake-wei 10000000000000000 \
      --bet-gas-wei 50000000000000 \
      --claim-gas-wei 30000000000000 \
      --inclusion-latency-seconds 2

Automatic current target: omit --target-epoch. --now-timestamp exists only as a deterministic test/replay override.

When no target is currently eligible the command reconciles prior settlements, reports no_eligible_target, and exits successfully.

## Continuous Stage 4 runtime

src/pancake_prediction/shadow_runtime.py composes one complete cycle:

1. validate the retry-safe ClickHouse schema;
2. incrementally synchronize anchored BSC Prediction + Chainlink evidence;
3. prospectively synchronize Binance Spot;
4. prospectively synchronize Binance Perp unless disabled;
5. reload canonical research inputs;
6. reconcile any previously recorded predictions whose rounds are now settled;
7. select an eligible current target;
8. derive the exact feature epochs allowed by the target's purge/settlement cutoff;
9. build a target-bounded research dataset only for those epochs;
10. fit, calibrate, project the final pool, and calculate EV;
11. re-check the deadline;
12. append the prediction only if it was still timely;
13. audit the ledger and evaluate the Stage 4 campaign gate.

### Target-bounded dataset path

A live target does **not** change the model training semantics to a fixed recent-N window.

The current baseline intentionally fits on every ResearchFeatureRow that is already settled, outside the purge zone, and available before the target decision. Removing older eligible rows would change model/calibrator identity and therefore is a model change, not a runtime optimization.

Instead, `required_shadow_feature_epochs()` declares the exact eligible training epochs plus the target epoch. `build_chunked_clickhouse_research_dataset(..., required_epochs=...)` keeps the full canonical replay/events for prior-history semantics, but filters expensive Binance ClickHouse chunk reads and alpha construction to those required epochs.

The build report exposes:

- requested_epoch_count;
- requested_epoch_min;
- requested_epoch_max;
- chunks_loaded;
- max Spot/Perp rows loaded per chunk;
- query time bounds.

Regression tests require the bounded feature rows and resulting `ShadowInferenceResult` to match the full-path result exactly.

### Single-target pool projection

Historical OOS evaluation still uses `build_oos_pool_projections()` to produce projections across a complete replay.

Stage 4 live inference needs only the current target. It therefore uses `build_oos_pool_projection_for_target()`, which calls the same internal target-projection implementation as the full OOS builder.

The single-target path preserves:

- the same pre-lock decision snapshot;
- the same purge boundary;
- only prior rounds settled before the target decision;
- the same trailing projection training window;
- the same median Bull/Bear growth estimator;
- the same projection model ID and train-max epoch.

A shared `BacktestEventIndex` is reused across the target and prior-round snapshots so the live path does not repeatedly rebuild event lookup structures.

Regression tests require the single-target result to equal `build_oos_pool_projections(...)[target_epoch]` exactly and require target final-pool changes to remain irrelevant.

### Runtime latency Evidence

Each Stage 4 runtime cycle records phase latency using `time.perf_counter_ns()`, which is monotonic and used only for performance duration.

Decision eligibility and deadline checks continue to use the existing UNIX wall clock. The two clocks are intentionally not interchangeable.

The runtime report includes a `timing` object with:

- `clock="monotonic_perf_counter"`;
- `phase_durations_ms` for only the phases that actually executed;
- `total_duration_ms`;
- `decision_to_completion_ms` when a target exists;
- `submission_margin_ms` when a target exists.

Instrumented phases include schema validation, BSC sync, Binance Spot/Perp sync, live coverage, canonical input load, settlement reconciliation, target selection, required-epoch planning, bounded dataset build, inference, deadline check, ledger append, and campaign audit.

Early exits remain semantically visible. For example, `source_warmup` has no target/dataset/inference timings, while `no_eligible_target` has target-selection timing but no dataset/inference timings.

`submission_margin_ms <= 0` means the submission-equivalent deadline has been reached or missed. Stage 4 still does not sign or broadcast any transaction.

### Campaign start preflight

Before starting a long-running campaign, the same runtime configuration can be checked without entering the mutating live cycle:

    pcs-shadow-runtime \
      --market BNBUSD \
      --canonical-db artifacts/bnbusd-history.sqlite \
      --shadow-db artifacts/shadow.sqlite3 \
      --stake-wei 10000000000000000 \
      --bet-gas-wei 50000000000000 \
      --claim-gas-wei 30000000000000 \
      --inclusion-latency-seconds 2 \
      --preflight-only \
      --preflight-output evidence/stage4-shadow-preflight.json

The preflight reuses the exact `ShadowRuntimeConfig` selected for the real runtime. It is read-only:

- it does not create or initialize the canonical SQLite database;
- it does not initialize or write the Shadow Ledger;
- it does not collect Prediction or Chainlink logs;
- it does not ingest Binance trades into ClickHouse;
- it does not build a target prediction;
- it does not update runtime or campaign Evidence.

It checks the existing canonical database and source-anchor metadata, configured historical sample capacity, active Chainlink history, BSC chain/head connectivity, retry-safe ClickHouse schema, configured Spot/Perp lineage presence, read-only one-row Binance Spot/Perp endpoint probes, and the selected `--shadow-db` campaign-manifest compatibility.

Shadow Ledger inspection uses a SQLite URI `mode=ro` path rather than the normal runtime connection, so preflight does not enable WAL mode, initialize schema, bind a manifest, or repair state. The compatibility states are:

- missing Shadow DB: compatible new-campaign state; no file is created;
- existing empty unbound ledger: compatible because the first normal runtime cycle can bind the expected manifest;
- event-bearing unbound ledger: incompatible because historical campaign semantics are ambiguous;
- bound ledger with the exact expected canonical manifest/digest: compatible;
- conflicting or malformed manifest / invalid Shadow Ledger schema: incompatible.

The preflight report exposes the expected manifest digest, stored manifest digest, ledger binding state and event count, and `shadow_campaign_compatible` participates in the overall `ready` result.

A ready preflight means only **structural campaign-start readiness**. It proves that the selected existing Shadow Ledger is compatible with the expected campaign identity, but it does not prove that the live oracle route still matches the stored anchor, because the first normal runtime chain sync remains the authoritative fail-closed route-stability check. It also does not satisfy prospective live flow warmup, prove that a current target is inferable, prove complete historical feature coverage, establish profitability, or authorize funded execution.

The command exits 0 when all mandatory structural checks pass and 2 when any check is incomplete. `--preflight-output` is written atomically and cannot be combined with cycle/campaign Evidence outputs.

The preflight also constructs the exact **expected campaign manifest** from the existing oracle anchors and current runtime configuration and reports its canonical payload and SHA-256 digest. It does not initialize or bind the Shadow Ledger.

### Immutable campaign identity

A real Stage 4 runtime binds one immutable semantic campaign manifest to the Shadow Ledger before settlement reconciliation or any new prediction append.

The manifest binds the settings that define the decision/economic contract:

- BSC chain, market, Prediction contract, oracle proxy anchor and Chainlink aggregator anchor;
- chain confirmation policy and reorg lookback;
- Binance Spot lineage and, when enabled, USD-M Perp lineage;
- feature timing / freshness / Chainlink hazard assumptions;
- the complete Shadow inference configuration, including training, calibration, purge, pool projection, stake, gas, decision lead, inclusion latency and minimum EV;
- the complete Stage 4 campaign evaluation policy.

Restarting against the same ledger is idempotent only when the canonical manifest is identical. A semantic change creates a different digest and the runtime fails closed before settlement reconciliation.

Performance-only tuning that does not change decision semantics is intentionally excluded from campaign identity. Examples include chain log chunk size, Binance HTTP bootstrap/page/batch limits and ClickHouse dataset chunk span. The chain reorg lookback is deliberately included because it changes the source-integrity/reconciliation boundary.

An existing event-bearing ledger without a campaign manifest is **not** automatically adopted. Its historical semantic identity cannot be established retrospectively, so the runtime requires a new manifest-bound campaign instead of guessing.

The ledger audit also checks that the requested audit `purge_rounds` matches the bound inference manifest, that prediction market identity matches the manifest, and that the Stage 4 campaign policy used for evaluation matches the policy bound in the manifest.

### Campaign single-writer runtime lock

Normal Stage 4 operation is campaign-single-writer.

Before any source synchronization, `pcs-shadow-runtime` acquires a non-blocking exclusive coordination lock derived from the resolved `--shadow-db` path:

    <shadow-db>.runtime-lock.sqlite3

The lock database is separate from the append-only Shadow Ledger because the runtime opens the real ledger through independent SQLite connections during reconciliation, audit and append. Holding an exclusive transaction on the real ledger would block the runtime itself.

The coordination DB uses a live SQLite `BEGIN EXCLUSIVE` transaction with zero busy timeout:

- the first runtime process acquires it and keeps it for the complete `--once` cycle or continuous loop;
- a second process targeting the same Shadow DB fails before chain/Binance synchronization starts;
- release occurs on normal exit, exception or connection/process termination;
- the lock DB file may remain after release; file existence alone does not mean a runtime still owns the campaign.

This coordination state is intentionally excluded from campaign manifest identity and campaign Evidence. It is operational process ownership, not decision semantics.

`--preflight-only` remains read-only and does not create or acquire the runtime lock.

The installed runtime command is:

    pcs-shadow-runtime

It accepts --once for one cycle or runs continuously at --poll-seconds intervals. Polling faster than one second is rejected.

Required endpoint configuration:

    export BSC_RPC_URL='...'
    export CLICKHOUSE_URL='http://127.0.0.1:8123'

Optional ClickHouse environment variables:

    export CLICKHOUSE_DATABASE='default'
    export CLICKHOUSE_USER='...'
    export CLICKHOUSE_PASSWORD='...'

Example one-cycle smoke run:

    pcs-shadow-runtime \
      --market BNBUSD \
      --canonical-db artifacts/bnbusd-history.sqlite \
      --shadow-db artifacts/shadow.sqlite3 \
      --once \
      --stake-wei 10000000000000000 \
      --bet-gas-wei 50000000000000 \
      --claim-gas-wei 30000000000000 \
      --inclusion-latency-seconds 2 \
      --evidence-output evidence/stage4-shadow-runtime-latest.json

Example continuous run:

    pcs-shadow-runtime \
      --market BNBUSD \
      --canonical-db artifacts/bnbusd-history.sqlite \
      --shadow-db artifacts/shadow.sqlite3 \
      --poll-seconds 1 \
      --stake-wei 10000000000000000 \
      --bet-gas-wei 50000000000000 \
      --claim-gas-wei 30000000000000 \
      --inclusion-latency-seconds 2 \
      --evidence-output evidence/stage4-shadow-runtime-latest.json

The evidence-output file is replaced atomically after each successful cycle. RPC and ClickHouse credentials are not included.

Long-running campaigns can also checkpoint campaign-level Evidence directly from the same
already-evaluated campaign report:

    pcs-shadow-runtime \
      --market BNBUSD \
      --canonical-db artifacts/bnbusd-history.sqlite \
      --shadow-db artifacts/shadow.sqlite3 \
      --poll-seconds 1 \
      --stake-wei 10000000000000000 \
      --bet-gas-wei 50000000000000 \
      --claim-gas-wei 30000000000000 \
      --inclusion-latency-seconds 2 \
      --campaign-evidence-output evidence/stage4-shadow-latest.json \
      --campaign-last-success-output evidence/stage4-shadow-last-success.json

`--campaign-evidence-output` is replaced atomically after every successful runtime cycle,
including cycles where the campaign gate is still incomplete. This makes progress observable
without treating insufficient sample coverage as a runtime failure.

`--campaign-last-success-output` is updated only when the Stage 4 campaign gate is actually
ready. A later incomplete cycle never overwrites or deletes the previously established
last-success artifact.

The runtime does not re-audit the campaign under a second configuration for checkpointing.
It serializes the `ShadowCampaignGateReport` already produced by the cycle, so the audit uses
the same inference `purge_rounds` and campaign policy that the runtime used. The two campaign
paths must be distinct from each other and from `--evidence-output`.

Campaign Evidence binds the append-only ledger through the immutable
`campaign_manifest_digest`, logical hash-chain `event_count` / `head_digest`, and
`campaign_digest`. The campaign digest itself includes the manifest identity. The retained
`ledger_sha256` is a SHA-256 of the SQLite main database file and is explicitly labeled as a
physical-file snapshot identifier. Because the ledger uses SQLite WAL mode, the manifest +
logical hash-chain binding is the authoritative campaign-state identity rather than the
physical-file hash alone.

Possible non-fatal cycle statuses include:

- no_eligible_target;
- target_not_ready;
- missed_submission_deadline;
- prediction_recorded.

Source-integrity failures such as an oracle-route change, a non-retry-safe ClickHouse schema, malformed source data, or incomplete live pagination are hard failures.

## Append-only Shadow Ledger

src/pancake_prediction/shadow_ledger.py stores predictions and later settlements in SQLite as a single append-only event stream.

Every event contains sequence, event kind, market and epoch, canonical JSON payload, previous event digest, and current SHA-256 event digest.

The ledger also stores the expected event count and head digest. Application-level SQLite triggers reject UPDATE and DELETE operations on event rows.

A separate singleton `shadow_campaign_manifest` binds canonical manifest JSON and its SHA-256 digest. UPDATE and DELETE are rejected by SQLite triggers. First binding is allowed only while the event ledger is empty; an identical retry is idempotent and a conflicting or retroactive binding is rejected.

Retries are idempotent: re-appending the exact same prediction or settlement returns the existing event, while a different payload for the same kind + market + epoch is rejected.

Settlement cannot be appended before its corresponding prediction. Settlement timestamps earlier than the prediction decision timestamp are rejected.

The audit recomputes the entire hash chain and reports prediction / settlement / unresolved counts, actionable Bull / Bear / Skip counts, model IDs and feature-set IDs, Brier score, directional accuracy, Shadow PnL coverage and aggregate PnL, campaign span, campaign-manifest identity, audit purge semantics, and integrity errors.

A bound manifest is also used to reject audit purge drift and prediction-market drift. The Stage 4 campaign gate independently requires the selected campaign policy digest to equal the policy digest stored in the manifest.

The audit always keeps profitability_gate_eligible=false, full_historical_gate_satisfied=false, signing_enabled=false, and live_broadcast=false.

## Settlement reconciliation

src/pancake_prediction/shadow_reconciliation.py converts later canonical replay results into Shadow settlement events.

It verifies:

- the prediction exists first;
- the canonical round is actually settled;
- the final pool total is internally consistent;
- reward fields, when present, match the decision-time treasury fee;
- replay integrity issues are absent.

Counterfactual Shadow PnL follows the same economic semantics as the backtest:

- correct Bull/Bear action: final parimutuel payout minus stake, bet gas, and claim gas;
- wrong action: minus stake and bet gas;
- tie: house win, therefore minus stake and bet gas for an actionable prediction;
- skip: zero PnL and not counted as actionable PnL coverage.

Manual reconciliation is available with:

    pcs-prediction shadow-reconcile \
      --market BNBUSD \
      --canonical-db artifacts/bnbusd-history.sqlite \
      --shadow-db artifacts/shadow.sqlite3

The continuous runtime performs the same reconciliation automatically before selecting the next target.

## Default Stage 4 campaign gate

src/pancake_prediction/shadow_campaign.py evaluates operational-readiness coverage. The default policy is intentionally independent of whether PnL is positive.

| Check | Default |
|---|---:|
| predictions | >= 1,000 |
| settlements | >= 900 |
| probability-scored settlements | >= 900 |
| actionable predictions | >= 200 |
| decision span | >= 7 days |
| unresolved rate | <= 100,000 ppm (10%) |
| Bull and Bear actions | both required |
| actionable settled PnL records | 100% required |
| model IDs in one campaign | <= 1 |
| feature-set IDs in one campaign | <= 1 |
| immutable campaign manifest | required |
| campaign policy matches manifest | required |

A campaign may pass this Stage 4 operational gate with negative PnL. That is deliberate. Stage 4 proves prospective operation and evidence completeness, not alpha.

The continuous runtime audits with the same purge boundary configured for inference. The ledger audit rejects a purge boundary different from the bound campaign manifest, and the campaign gate rejects an evaluation policy different from the bound manifest policy.

## Ledger CLI

Initialize a ledger:

    pcs-prediction shadow-ledger-init --db artifacts/shadow.sqlite3

Append a pre-decision record:

    pcs-prediction shadow-append-prediction \
      --db artifacts/shadow.sqlite3 \
      --record artifacts/prediction.json \
      --purge-rounds 2

Append the later settlement:

    pcs-prediction shadow-append-settlement \
      --db artifacts/shadow.sqlite3 \
      --record artifacts/settlement.json

Audit the ledger:

    pcs-prediction shadow-ledger-audit \
      --db artifacts/shadow.sqlite3 \
      --purge-rounds 2

Evaluate the default Stage 4 campaign gate:

    pcs-prediction shadow-campaign-gate \
      --db artifacts/shadow.sqlite3 \
      --purge-rounds 2

A manually initialized ledger that has never been bound by the Stage 4 runtime can still be inspected with the low-level ledger CLI, but it is intentionally **not eligible** to pass the Stage 4 campaign gate. Event-bearing unbound history cannot be retroactively promoted into a manifest-proven campaign.

## Evidence

scripts/build_shadow_campaign_evidence.py converts a Shadow ledger into a compact JSON Evidence artifact containing the ledger SHA-256, hash-chain head, policy, all Stage 4 checks, coverage metrics, probability metrics, observed Shadow PnL, and explicit safety/profitability boundaries.

Example:

    python scripts/build_shadow_campaign_evidence.py \
      --db artifacts/shadow.sqlite3 \
      --output evidence/stage4-shadow-latest.json

The script returns a non-zero status while the configured campaign gate is incomplete, but still writes the latest Evidence JSON so progress can be inspected.

## Prerequisites for a real long-running campaign

Before starting pcs-shadow-runtime:

1. bootstrap the canonical recent BSC dataset with a proven current Chainlink proxy -> aggregator route;
2. have enough settled historical canonical rounds for the selected min-train / calibration / pool-projection configuration;
3. have matching historical Binance Spot / Perp data in ClickHouse under the same timestamp-unit and availability-lag lineage selected by the runtime, and finish archive ingestion before any prospective live row exists for that lineage;
4. start the live runtime before expecting a prospectively valid current microstructure row, allowing at least the configured flow-lookback warmup.

Do not bootstrap a live campaign by fetching old REST trades after the fact and relabeling them as prospectively observed.

## What remains

The software boundary for continuous prospective Stage 4 operation is implemented.

What is not yet evidenced is a real long-running campaign satisfying the default Stage 4 policy. The next empirical milestone is to run pcs-shadow-runtime continuously against a prepared canonical SQLite database and ClickHouse instance, preserve its append-only Shadow ledger, and produce Stage 4 Evidence after the minimum campaign duration/sample requirements are met.

Only after that campaign is complete should later readiness stages treat Stage 4 as empirically cleared.

Any future transition to funded validation remains a separate explicit authorization and safety design decision.
