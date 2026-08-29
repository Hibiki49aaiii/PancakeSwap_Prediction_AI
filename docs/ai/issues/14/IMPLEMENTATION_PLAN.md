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
