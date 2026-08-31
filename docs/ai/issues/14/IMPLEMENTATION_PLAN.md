# Issue #14 Implementation Plan

## Goal

Prevent two normal Stage 4 runtime processes from operating the same Shadow campaign concurrently.

## Existing gap

SQLite transactions protect individual ledger writes, not the complete runtime cycle. Two processes can therefore perform source synchronization and inference concurrently before either reaches the append transaction.

## Architecture

### Separate SQLite coordination database

Derive one lock database path from the resolved Shadow Ledger path:

`<shadow-db>.runtime-lock.sqlite3`

The lock database is coordination state only. It is not part of campaign semantics or Evidence.

### Acquisition

A small standard-library-only lock object:

1. resolves the Shadow Ledger path;
2. creates the lock DB parent directory for normal runtime;
3. opens SQLite with `timeout=0` and autocommit mode;
4. initializes a trivial coordination table when needed;
5. acquires non-blocking `BEGIN EXCLUSIVE`;
6. holds the connection/transaction until the runtime process leaves its context.

A locked/busy result is translated to a controlled `ShadowRuntimeLockError` without leaking raw SQLite error text.

### Release

Context exit performs rollback/close. SQLite/OS locking is released automatically on normal exit, exception and process death. A persistent lock DB file is not treated as evidence of a live lock.

## CLI ordering

`--preflight-only` remains before lock acquisition and does not create a lock DB.

Normal runtime:

1. build/validate runtime configuration;
2. create source clients;
3. acquire campaign runtime lock;
4. enter `--once` or continuous cycle loop;
5. release lock on every exit path.

No source sync occurs until the exclusive lock has been acquired.

## Manifest / Evidence

The lock path and lock state are explicitly excluded from:

- campaign manifest digest;
- campaign gate;
- profitability/full-history Evidence.

This is operational coordination, not decision semantics.

## Tests

- deterministic same/different path derivation;
- first acquire succeeds;
- concurrent acquire fails immediately;
- release allows reacquire;
- exception context releases;
- CLI calls runtime only while lock context active;
- contention prevents runtime call;
- preflight-only does not construct/acquire lock.

## Safety

No signer, key, broadcast, funded execution or external dependency.

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
- base SHA: `9103713f04c4e1b3f23e4e6180b54d07299ab675`
- 449 tests / 87% / CI #1278 green.


# Implementation Result — 2026-08-29

Issue #14 implementation is complete.

## Implemented

- Added `src/pancake_prediction/shadow_runtime_lock.py`.
- `shadow_runtime_lock_path()` derives one deterministic coordination DB from the resolved Shadow Ledger path.
- `ShadowRuntimeProcessLock`:
  - creates only the coordination DB parent/file required by normal runtime;
  - opens SQLite with `timeout=0`;
  - acquires non-blocking `BEGIN EXCLUSIVE`;
  - keeps the transaction/connection for the complete runtime lifetime;
  - converts lock contention into controlled `ShadowRuntimeLockError`;
  - releases by rollback/connection close on normal or exceptional context exit.
- Lock state is not inferred from file existence.
- `pcs-shadow-runtime` acquires the lock before entering any normal runtime cycle.
- `--once` remains locked for the entire cycle.
- Continuous mode retains ownership across all polling cycles.
- Lock contention exits through the CLI error path before `run_shadow_runtime_cycle()` can execute.
- `--preflight-only` returns before the lock object is constructed and does not create the lock DB.
- The lock path/state is excluded from:
  - campaign manifest identity;
  - campaign gate;
  - campaign Evidence;
  - profitability/full-history gates.
- No external locking dependency or OS-specific `flock` was introduced.

## Files Changed

- `.ai/index.md`
- `.ai/observations/runtime-coordination-locks-are-operational-not-semantic.md`
- `docs/STAGE4_SHADOW.md`
- `docs/ai/issues/14/HUMAN_UNDERSTANDING.md`
- `docs/ai/issues/14/IMPLEMENTATION_PLAN.md`
- `src/pancake_prediction/shadow_runtime_cli.py`
- `src/pancake_prediction/shadow_runtime_lock.py`
- `tests/test_shadow_runtime_cli.py`
- `tests/test_shadow_runtime_lock.py`

## Verification

Production/test source SHA:
`05a7a6ea1790da69d250756d08e875fd5a62acd1`

Quality Evidence #286 / run `33243597869`:

- pytest: **455 passed**
- coverage: **87%**
- Ruff: success
- mypy strict: success
- Bandit: success
- pip-audit: success
- final quality gate: success

Full PR CI #1287 / run `33243600279`:

- pytest: **455 passed in 25.77s**
- coverage: **87%**
- test: success
- ClickHouse integration: success
- Gitleaks: success
- pinned legacy **144,000-round** audit: success
- overall CI: success

The earlier Quality Evidence #284 correctly exposed two test-only static-quality regressions introduced during implementation:

- Ruff SIM117 for nested context managers;
- mypy errors caused by referring to lock API through the CLI module rather than importing it directly.

The implementation was not weakened. Tests were corrected, then the full suite and security/integration gates passed.

## Post-Implementation Review

### Correctness

The process lock covers the full runtime cycle rather than only ledger append, preventing duplicate local runtime processes from racing through source synchronization and inference.

### Failure behavior

Contention is non-blocking and fail-closed before `run_shadow_runtime_cycle()`. A stale coordination DB file is harmless because the live SQLite exclusive transaction, not file presence, is authoritative.

### Architecture

Process coordination remains separate from the append-only ledger and immutable campaign manifest. This prevents operational deployment mechanics from contaminating campaign semantic identity.

### Preflight

Read-only preflight neither creates nor acquires the runtime lock.

### Security

No private key, signer, transaction signing, mainnet broadcast, funded execution, credential issuance/change, profitability promotion, or historical-source promotion was introduced.

### Scope boundary

This is a local filesystem/SQLite single-writer guarantee for one resolved Shadow DB path. It is not a distributed multi-host lease.
