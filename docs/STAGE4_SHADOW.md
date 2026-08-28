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
- live_broadcast=false.

The Stage 5 loopback/local-fork execution path remains separate.

## Decision path

    canonical Prediction + active Chainlink history
                      |
    checksum/provenance-bound Binance market data
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

## Append-only Shadow Ledger

src/pancake_prediction/shadow_ledger.py stores predictions and later settlements in SQLite as a single append-only event stream.

Every event contains sequence, event kind, market and epoch, canonical JSON payload, previous event digest, and current SHA-256 event digest.

The ledger also stores the expected event count and head digest. Application-level SQLite triggers reject UPDATE and DELETE operations on event rows.

Retries are idempotent: re-appending the exact same prediction or settlement returns the existing event, while a different payload for the same kind + market + epoch is rejected.

Settlement cannot be appended before its corresponding prediction. Settlement timestamps earlier than the prediction decision timestamp are rejected.

The audit recomputes the entire hash chain and reports prediction / settlement / unresolved counts, actionable Bull / Bear / Skip counts, model IDs and feature-set IDs, Brier score, directional accuracy, Shadow PnL coverage and aggregate PnL, campaign span, and integrity errors.

The audit always keeps profitability_gate_eligible=false, full_historical_gate_satisfied=false, signing_enabled=false, and live_broadcast=false.

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

## CLI

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

## Canonical data to Shadow decision

When canonical Prediction / Chainlink data is available in SQLite and Binance research data is present in ClickHouse, a target decision can be generated and appended atomically:

    export CLICKHOUSE_URL=http://127.0.0.1:8123

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

The command uses the same canonical dataset builders as Stage 2/3, then calls the single-target Shadow inference boundary and appends the deterministic ResearchPredictionRecord to the Shadow ledger.

## Evidence

scripts/build_shadow_campaign_evidence.py converts a Shadow ledger into a compact JSON Evidence artifact containing the ledger SHA-256, hash-chain head, policy, all Stage 4 checks, coverage metrics, probability metrics, observed Shadow PnL, and explicit safety/profitability boundaries.

Example:

    python scripts/build_shadow_campaign_evidence.py \
      --db artifacts/shadow.sqlite3 \
      --output evidence/stage4-shadow-latest.json

The script returns a non-zero status while the configured campaign gate is incomplete, but still writes the latest Evidence JSON so progress can be inspected.

## What remains

The software boundary for prospective Shadow decisions is implemented. What is not yet evidenced is a real long-running campaign satisfying the default Stage 4 policy.

The next operational work is to run repeated canonical collection + shadow-infer, reconcile each completed round with a settlement record, and preserve the resulting Stage 4 Evidence. Only after that campaign is complete should later readiness stages treat Stage 4 as empirically cleared.

Any future transition to funded validation remains a separate explicit authorization and safety design decision.
