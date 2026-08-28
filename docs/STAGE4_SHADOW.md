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

The CLI entrypoint is:

    pcs-prediction shadow-chain-sync \
      --market BNBUSD \
      --db artifacts/bnbusd-history.sqlite

BSC_RPC_URL is read from the environment unless --rpc-url is supplied. The endpoint is never included in the JSON report.

## Prospective Binance live collection

src/pancake_prediction/binance_live.py writes live aggregate trades into the same retry-safe ClickHouse binance_agg_trades table used by the research pipeline.

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
8. build the heavy research dataset only when a target window is open;
9. fit, calibrate, project the final pool, and calculate EV;
10. re-check the deadline;
11. append the prediction only if it was still timely;
12. audit the ledger and evaluate the Stage 4 campaign gate.

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

Retries are idempotent: re-appending the exact same prediction or settlement returns the existing event, while a different payload for the same kind + market + epoch is rejected.

Settlement cannot be appended before its corresponding prediction. Settlement timestamps earlier than the prediction decision timestamp are rejected.

The audit recomputes the entire hash chain and reports prediction / settlement / unresolved counts, actionable Bull / Bear / Skip counts, model IDs and feature-set IDs, Brier score, directional accuracy, Shadow PnL coverage and aggregate PnL, campaign span, and integrity errors.

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

A campaign may pass this Stage 4 operational gate with negative PnL. That is deliberate. Stage 4 proves prospective operation and evidence completeness, not alpha.

The continuous runtime audits with the same purge boundary configured for inference. A custom purge boundary therefore cannot accidentally be audited using the default value.

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
3. have matching historical Binance Spot / Perp data in ClickHouse under the same timestamp-unit and availability-lag lineage selected by the runtime;
4. start the live runtime before expecting a prospectively valid current microstructure row, allowing at least the configured flow-lookback warmup.

Do not bootstrap a live campaign by fetching old REST trades after the fact and relabeling them as prospectively observed.

## What remains

The software boundary for continuous prospective Stage 4 operation is implemented.

What is not yet evidenced is a real long-running campaign satisfying the default Stage 4 policy. The next empirical milestone is to run pcs-shadow-runtime continuously against a prepared canonical SQLite database and ClickHouse instance, preserve its append-only Shadow ledger, and produce Stage 4 Evidence after the minimum campaign duration/sample requirements are met.

Only after that campaign is complete should later readiness stages treat Stage 4 as empirically cleared.

Any future transition to funded validation remains a separate explicit authorization and safety design decision.
