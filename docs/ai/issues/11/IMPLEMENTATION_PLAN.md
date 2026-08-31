# Issue #11 Implementation Plan

## Goal

Add a read-only structural preflight for the exact Stage 4 runtime configuration before a long-running prospective campaign is started.

## Boundary

The preflight may perform read-only network/database calls, but it must not:

- create or initialize the canonical SQLite database;
- initialize or write the Shadow Ledger;
- collect Prediction/Chainlink events;
- ingest Binance rows into ClickHouse;
- run target inference;
- update cycle/campaign Evidence;
- sign or broadcast a transaction.

## Reused configuration

The command surface remains `pcs-shadow-runtime`.

New flags:

- `--preflight-only`
- `--preflight-output <path>`

All existing market, source-lineage, model-history, purge, gas/fee, and latency options are parsed through the same `ShadowRuntimeConfig` builder as the live runtime.

This avoids a second preflight-only configuration model.

## Core API

Create `src/pancake_prediction/shadow_preflight.py` with a pure orchestration function:

`run_shadow_runtime_preflight(rpc, clickhouse, binance, market, canonical_database, *, config)`

The function returns a frozen report with individual boolean checks, observed counts/anchors, and explicit limitations.

## Checks

### Canonical SQLite

Before any SQLite connection:

1. require `canonical_database.is_file()`;
2. only then read metadata and canonical research inputs.

Read and validate:

- `<market>.last_collected_block`;
- `<market>.oracle_proxy_anchor_address`;
- `<market>.oracle_anchor_address`.

Count:

- replay rounds;
- settled Bull/Bear rounds;
- active Chainlink AnswerUpdated history.

Structural history capacity:

- model/calibration settled requirement = `min_train_rounds + calibration_rounds`;
- pool requirement = `pool_min_train_rounds`;
- required settled history = max of those;
- replay capacity additionally requires `purge_rounds + target` room.

This is only a structural lower bound. It does not assert that every historical round has a valid ResearchFeatureRow.

### BSC RPC

Read only:

- `chain_id()` must equal 56;
- `block_number()` must be >= canonical `last_collected_block`.

Do not collect logs or mutate canonical storage.

### ClickHouse

Reuse `inspect_binance_trade_schema()`.

Require retry-safe `binance_agg_trades` schema.

For each enabled configured lineage, issue a parameterized `FINAL` aggregation query for:

- row count;
- first trade timestamp;
- last trade timestamp.

Require at least one row for Spot and, when enabled, USD-M Perp.

### Binance public API

Use the existing `BinanceAggTradeSource` interface.

Read only:

- Spot `limit=1`;
- Perp `limit=1` when enabled.

Require a non-empty result.

No fetched rows are persisted.

## Failure / secret handling

Expected external failures are represented as failed checks with generic classifications.

Do not serialize raw exception text for RPC, ClickHouse, or Binance failures because provider URLs may contain credentials/tokens.

Report must never include:

- BSC RPC URL;
- ClickHouse endpoint;
- username/password;
- private keys or wallet material.

## Readiness semantics

`ready=true` only when every mandatory structural/connectivity check is true.

Explicit limitations:

- current oracle route stability is still proven by the first normal runtime chain sync;
- prospective live flow warmup is not satisfied by preflight;
- current target inferability is not proven;
- profitability is not proven;
- funded execution is not authorized.

## CLI behavior

When `--preflight-only`:

1. validate evidence path rules;
2. create read-only clients;
3. run preflight;
4. print canonical JSON;
5. atomically write `--preflight-output` when supplied;
6. exit 0 when ready, 2 otherwise.

Reject ambiguous combinations with cycle/campaign Evidence output flags.

When `--preflight-only` is absent, existing runtime behavior remains unchanged.

## Tests

- fully ready preflight;
- missing canonical DB does not create a file;
- wrong BSC chain and stale head fail;
- invalid/missing anchors fail;
- insufficient settled/Chainlink history fails;
- schema/lineage failures fail;
- Binance probe failure fails;
- disabled Perp is not required;
- report omits credentials;
- CLI preflight does not call runtime cycle;
- atomic output and output-path validation;
- existing runtime CLI tests remain green.

## Verification

- Ruff
- mypy strict
- pytest + coverage
- Bandit
- pip-audit
- Gitleaks
- ClickHouse integration
- pinned 144,000-round audit

## Base

- Branch: `agent/v0.7-alpha-research`
- Base HEAD: `4eb6ddf61b9f0a89bd29aad3e80641cd188453a7`
- Verified implementation source below that head: `f2e68d42ce8378facaf1af8ed7a6aa42c65f13bf`
- Existing suite: 409 tests / 87% coverage.

# Implementation Result — 2026-08-29

Issue #11 implementation is complete.

## Implemented

- Added `src/pancake_prediction/shadow_preflight.py`.
- Added `pcs-shadow-runtime --preflight-only`.
- Added optional atomic `--preflight-output`.
- Reused the live runtime's existing parser and `ShadowRuntimeConfig`; no second preflight-only config model was created.
- Preflight checks:
  - canonical DB already exists before any SQLite connection;
  - canonical research inputs are loadable;
  - replay / settled Bull-Bear / Chainlink history capacity;
  - valid `last_collected_block`, oracle proxy anchor and Chainlink aggregator anchor;
  - BSC chain id 56 and head not behind canonical checkpoint;
  - retry-safe ClickHouse `binance_agg_trades` schema;
  - configured Spot lineage presence;
  - configured USD-M lineage presence when Perp is enabled;
  - read-only Binance Spot / USD-M `limit=1` probes.
- Preflight does not initialize canonical storage or the Shadow Ledger, collect chain logs, ingest Binance rows, infer a target, or checkpoint campaign Evidence.
- External provider exception text is not serialized, preventing endpoint credentials/tokens from leaking into preflight Evidence.
- Added explicit report limitations so `ready=true` cannot be confused with live warmup, route stability, current-target inferability, profitability, or funded execution readiness.
- Added reusable External Intelligence observation:
  - `.ai/observations/read-only-preflight-must-not-initialize-storage.md`
  - indexed in `.ai/index.md`.

## Files Changed

- `.ai/index.md`
- `.ai/observations/read-only-preflight-must-not-initialize-storage.md`
- `docs/STAGE4_SHADOW.md`
- `docs/ai/issues/11/HUMAN_UNDERSTANDING.md`
- `docs/ai/issues/11/IMPLEMENTATION_PLAN.md`
- `src/pancake_prediction/shadow_preflight.py`
- `src/pancake_prediction/shadow_runtime_cli.py`
- `tests/test_shadow_preflight.py`
- `tests/test_shadow_runtime_cli.py`

No DB schema migration, new dependency, signer, wallet, or broadcast path was introduced.

## Verification

Implementation source SHA `cab73adcc37087c8bec9bf4711542348dcd0e9e9`:

Quality Evidence #254 / run `33241280428`:

- pytest: **418 passed**
- coverage: **87%**
- Ruff: success
- mypy strict: success
- Bandit: success
- pip-audit: success
- final quality gate: success

Full PR CI #1229 on the same implementation SHA:

- test: success
- ClickHouse integration: success
- Gitleaks: success
- pinned legacy **144,000-round** audit: success

After adding the reusable `.ai` observation/index entry, exact docs head
`3a60e4a4a39357e531f99c313279e27dad496414` was verified again by full
PR CI #1232 / run `33241544069`:

- **418 passed in 25.15s**
- **87% coverage**
- Ruff / mypy strict / Bandit / pip-audit: success
- ClickHouse integration: success
- Gitleaks: success
- pinned legacy **144,000-round** audit: success
- overall CI: success

## Post-Implementation Review

### Correctness

The preflight checks only structural campaign-start prerequisites. It does not suppress the normal runtime's fail-closed oracle-route proof or source warmup requirements.

### Regression

Normal runtime execution remains on the existing path when `--preflight-only` is absent. Existing campaign checkpoint semantics remain unchanged.

### Architecture

Read-only orchestration is isolated in `shadow_preflight.py`; filesystem output remains at the CLI boundary. Runtime configuration remains a single source of truth.

### Security

No signer/private key/mainnet transaction path was added. Provider exception messages are not included in the JSON report. Missing canonical storage is detected before any helper can create it.

### Remaining external blockers

The authenticated historical BSC RPC blocker remains separate. This preflight does not create credentials and does not promote full historical or profitability gates.

