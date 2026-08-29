# Issue #13 Implementation Plan

## Goal

Make Stage 4 preflight prove that the selected existing Shadow Ledger is compatible with the exact campaign manifest the normal runtime would bind.

## Existing gap

Issue #12 makes normal runtime fail closed on campaign-manifest drift, but the preflight does not currently inspect `--shadow-db`.

This means a preflight can pass even though the next normal runtime cycle will later reject the ledger after chain/Binance synchronization.

## Architecture

### 1. Read-only ledger inspection API

Add a small public inspection helper in `shadow_ledger.py`.

It must not reuse `ShadowLedgerStore._connect()`, because that path executes `PRAGMA journal_mode=WAL` and is therefore not a pure read-only preflight primitive.

Use SQLite URI read-only mode:

`file:<absolute-path>?mode=ro`

Inspection returns:

- database exists;
- ledger schema readability;
- event count;
- stored manifest digest;
- stored canonical manifest payload when valid;
- binding state;
- inspection errors.

### 2. State semantics

- missing DB -> `absent`, compatible with starting a new campaign;
- valid empty ledger without manifest -> `empty_unbound`, compatible because normal runtime can bind safely;
- event-bearing ledger without manifest -> `event_bearing_unbound`, incompatible;
- valid exact bound manifest -> `bound`, compatible if exact canonical payload + digest match;
- malformed/incomplete schema or malformed manifest -> `invalid`, incompatible.

An arbitrary existing SQLite file that is not a Shadow Ledger is not treated as an empty new campaign.

### 3. Preflight integration

Pass the existing CLI `--shadow-db` into `run_shadow_runtime_preflight()`.

The preflight:

1. builds the exact expected manifest from canonical anchors + runtime config;
2. read-only inspects the Shadow Ledger;
3. computes `shadow_campaign_compatible`;
4. includes expected/stored digest and binding state in JSON Evidence;
5. makes compatibility part of `ready`.

No new CLI flag.

### 4. No mutation invariant

Regression tests prove:

- missing path remains missing;
- existing DB main-file bytes remain unchanged;
- event count / manifest row remain unchanged;
- no schema migration is performed by preflight;
- no normal writer connection is used.

## Error handling

SQLite errors, malformed canonical JSON, digest mismatch, missing required ledger tables, or multiple/invalid singleton rows fail closed into the report without serializing raw provider/credential data.

## Safety

No signer, key, broadcast, funded execution or source credential changes.

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
- base SHA: `ca9b7814a475f7650a3491564e97317d74f97686`
- 441 tests / 87% / CI #1266 green.
