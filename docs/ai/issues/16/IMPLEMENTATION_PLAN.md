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


# Implementation Result — 2026-08-29

Issue #16 implementation is complete.

## Implemented

- Extracted reusable `SqliteExclusiveProcessLock` into `process_lock.py`.
- Existing `ShadowRuntimeProcessLock` now reuses the generic primitive without changing its external behavior.
- Added `binance_live_lock.py` with:
  - read-only `ClickHouseLineageTarget` Protocol;
  - canonical ClickHouse endpoint normalization;
  - canonical Binance live lineage identity;
  - SHA-256 lineage digest;
  - OS-temp digest-only lock path;
  - `BinanceLiveLineageProcessLock`.
- Lineage identity binds:
  - normalized ClickHouse endpoint;
  - ClickHouse database;
  - market;
  - Binance symbol;
  - venue;
  - timestamp unit;
  - availability lag.
- ClickHouse username/password do not participate in lock identity.
- Default HTTP/HTTPS ports and trailing root slash normalize to the same endpoint identity.
- Lock filename exposes only a 64-hex SHA-256 digest.
- Same lineage contention is non-blocking and fail-closed.
- Different lineages can be locked independently.
- `pcs-shadow-runtime` now acquires:
  1. Shadow campaign runtime lock;
  2. Spot live-lineage lock;
  3. Perp live-lineage lock when enabled;
  before entering any runtime cycle.
- `--no-perp` acquires only Spot.
- `--preflight-only` returns before either runtime or lineage lock construction.
- `pcs-clickhouse binance-live-sync` performs schema validation, then acquires the exact lineage lock before constructing/fetching live Binance rows.
- Manual CLI contention prevents `sync_binance_live_aggtrades()` from executing.
- Runtime contention prevents `run_shadow_runtime_cycle()` from executing.
- Lock mechanism remains excluded from campaign manifest and Evidence semantics.

## Files Changed

- `.ai/index.md`
- `.ai/observations/prospective-observation-lineages-need-single-writer-coordination.md`
- `docs/STAGE4_SHADOW.md`
- `docs/ai/issues/16/HUMAN_UNDERSTANDING.md`
- `docs/ai/issues/16/IMPLEMENTATION_PLAN.md`
- `src/pancake_prediction/binance_live_lock.py`
- `src/pancake_prediction/clickhouse_cli.py`
- `src/pancake_prediction/process_lock.py`
- `src/pancake_prediction/shadow_runtime_cli.py`
- `src/pancake_prediction/shadow_runtime_lock.py`
- `tests/test_binance_live_lock.py`
- `tests/test_campaign_cli.py`
- `tests/test_clickhouse_cli.py`
- `tests/test_shadow_runtime_cli.py`

## Verification

Production/test source SHA:
`32c998f3b556f496d63b6061b5b1400ebe73b8be`

Quality Evidence #304 / run `33249529724`:

- pytest: **469 passed**
- coverage: **87%**
- Ruff: success
- mypy strict: success
- Bandit: success
- pip-audit: success
- final quality gate: success

Full PR CI #1325 / run `33249531556`:

- pytest: **469 passed in 17.37s**
- coverage: **87%**
- test: success
- ClickHouse integration: success
- Gitleaks: success
- pinned legacy **144,000-round** audit: success
- overall CI: success

## Implementation corrections from quality review

The first integrated version exposed three useful test/design issues:

1. lineage lock typing was too concrete and rejected test/client implementations that only need `endpoint/database`;
2. an existing live-sync CLI fake lacked the new lineage identity fields;
3. fixed credential literals in a security test triggered S106.

The final design uses a narrow read-only Protocol, updates the existing fake surface, and tests credential exclusion without hardcoded secret literals.

A second mypy pass showed the Protocol attributes needed read-only property semantics to accept frozen test clients. That was corrected without weakening the lock boundary.

## Post-Implementation Review

### Correctness

Two official local writers cannot concurrently fetch/insert the same prospective Binance lineage. This prevents duplicate trade IDs with different actual observation times from being resolved solely by ClickHouse ingest-version ordering.

### Architecture

Process coordination is shared across both runtime and manual live-sync entrypoints while remaining separate from prediction/campaign semantic identity.

### Privacy

The local lock filename contains no raw endpoint, database name, username, password or credential; only the SHA-256 digest is used.

### Backward compatibility

Read/query/archive-ingest paths are unchanged. Existing Shadow runtime campaign locking remains intact and now reuses the common SQLite primitive.

### Safety

No signer, private key, mainnet transaction signing, broadcast, funded execution, credential issuance/change, profitability promotion or historical-source promotion was introduced.

### Remaining boundary

This protects official writers on one host. Distributed multi-host live writers remain out of scope.
