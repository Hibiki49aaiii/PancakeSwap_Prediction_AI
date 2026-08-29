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
