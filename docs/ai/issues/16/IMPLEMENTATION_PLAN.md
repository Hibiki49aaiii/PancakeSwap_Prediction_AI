# Issue #16 Implementation Plan

## Goal

Prevent two official local processes from prospectively writing the same Binance live ClickHouse lineage concurrently.

## Existing gap

The ClickHouse table uses `ReplacingMergeTree(ingest_version)` keyed by:

- venue;
- symbol;
- timestamp unit;
- availability lag;
- aggregate trade ID.

Two writers can read the same cursor, fetch the same Binance trade IDs at different actual HTTP observation times, and insert duplicate versions. Which availability timestamp survives then depends on replacement version rather than one controlled prospective writer.

## Architecture

### 1. Reusable SQLite process-lock primitive

Extract the tested SQLite exclusive-lock mechanics from the Stage 4 Shadow runtime lock into a generic internal primitive.

Requirements:

- standard library only;
- deterministic separate lock DB;
- `timeout=0`;
- `BEGIN EXCLUSIVE`;
- controlled contention/failure errors;
- release by rollback + close;
- stale file is not ownership.

The existing Shadow runtime wrapper keeps its public behavior/tests.

### 2. Binance live lineage identity

Add `binance_live_lock.py`.

Canonical identity:

- normalized ClickHouse endpoint;
- ClickHouse database;
- market;
- venue;
- timestamp unit;
- availability lag.

The identity is canonical JSON hashed with SHA-256. Only the digest appears in the lock filename.

Default lock root is a dedicated directory under the OS temporary directory. Tests can supply a temporary root explicitly.

Endpoint normalization makes semantically equivalent default-port/root forms map consistently where practical.

Credentials are excluded. `ClickHouseHttpClient` already forbids credentials embedded in the endpoint URL.

### 3. Shadow runtime integration

The CLI, not the pure cycle function, owns process coordination.

For normal runtime:

1. acquire Shadow campaign lock;
2. acquire Spot lineage lock;
3. acquire Perp lineage lock if enabled;
4. enter the once/continuous runtime loop.

Acquire in a deterministic Spot-then-Perp order and release in reverse via context management.

Preflight returns before any process lock acquisition.

### 4. ClickHouse CLI integration

`pcs-clickhouse binance-live-sync`:

1. schema check;
2. acquire exactly one lineage lock;
3. call `sync_binance_live_aggtrades()`;
4. release.

Contention must happen before Binance HTTP fetch.

## Scope boundary

This is same-host process coordination for official entrypoints. It does not provide a distributed lease and does not redesign ClickHouse replacement semantics.

Sequential archive ingestion into an already-live lineage remains separate work.

## Tests

- generic lock regression via existing Shadow lock tests;
- lineage path normalization/determinism;
- same lineage contention;
- different lineage independent locks;
- endpoint/database differences produce different locks;
- filename does not expose endpoint;
- runtime holds both configured lineage locks during cycle;
- runtime contention prevents cycle;
- no-Perp acquires only Spot;
- preflight constructs no lineage lock;
- ClickHouse live CLI holds lock during sync;
- ClickHouse live CLI contention prevents sync.

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

- branch: `agent/v0.7-alpha-research`
- base SHA: `7be4a6eb664e651621d31b946e16b51ac45b6278`
- 461 tests / 87% / CI #1305 green.
