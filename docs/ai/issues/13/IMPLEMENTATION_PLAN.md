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


# Implementation Result — 2026-08-29

Issue #13 implementation is complete.

## Implemented

- Added `ShadowLedgerReadOnlyInspection`.
- Added `inspect_shadow_ledger_read_only()` using SQLite URI `mode=ro` plus query-only semantics.
- The inspection path deliberately does not use `ShadowLedgerStore._connect()`, so it does not enable WAL mode or run runtime initialization.
- Existing Shadow Ledger state classification:
  - `absent`;
  - `empty_unbound`;
  - `event_bearing_unbound`;
  - `bound`;
  - `invalid`.
- Core Shadow Ledger schema/state consistency is checked before manifest compatibility.
- Canonical manifest JSON/digest is validated through the same ledger manifest parser used by runtime audit.
- `run_shadow_runtime_preflight()` now receives the existing `--shadow-db`.
- Preflight computes `shadow_campaign_compatible`:
  - missing DB -> compatible;
  - empty unbound -> compatible;
  - exact bound manifest -> compatible;
  - event-bearing unbound -> fail closed;
  - conflicting bound manifest -> fail closed;
  - malformed/invalid ledger -> fail closed.
- The JSON report includes:
  - Shadow DB path;
  - database existence;
  - schema readiness;
  - binding state;
  - event count;
  - stored manifest digest/payload;
  - sanitized inspection error codes.
- Expected campaign manifest/digest remains built by the same shared runtime manifest builder.
- CLI adds no new flag; the existing `--shadow-db` is passed to preflight.
- Regression tests prove:
  - missing DB is not created;
  - exact bound manifest is accepted;
  - conflicting manifest is rejected;
  - empty unbound ledger is accepted;
  - event-bearing unbound ledger is rejected;
  - malformed manifest is rejected;
  - non-Shadow SQLite schema is rejected;
  - existing main DB bytes and logical event/manifest identity remain unchanged during inspection.

## Verification

Production/test source SHA:
`cfcc6e622baa01a0bbdf7ec6dc1b0d428d62b385`

Quality Evidence #280 / run `33242957497`:

- pytest: **449 passed**
- coverage: **87%**
- Ruff: success
- mypy strict: success
- Bandit: success
- pip-audit: success
- final quality gate: success

Full PR CI #1274 / run `33242959368`:

- pytest: **449 passed in 28.84s**
- coverage: **87%**
- test: success
- ClickHouse integration: success
- Gitleaks: success
- pinned legacy **144,000-round** audit: success
- overall CI: success

## Post-Implementation Review

### Correctness

Preflight now catches campaign-manifest incompatibility before the normal runtime enters source synchronization. The normal runtime's independent fail-closed manifest bind remains unchanged as the authoritative mutation boundary.

### Read-only semantics

Missing storage is not created, existing storage is not migrated, and the inspection path avoids the runtime WAL-setting connection helper.

### Backward compatibility

An empty legacy ledger remains safely bindable. Event-bearing unbound history is still inspectable but cannot be treated as a proven campaign.

### Security

Raw SQLite/provider exception strings are not serialized; only controlled error codes enter the report. No credentials, signer, transaction signing, mainnet broadcast or funded execution were introduced.

### Remaining external boundaries

This does not prove live oracle-route stability, prospective source warmup, current target inferability, long-running default Stage 4 campaign completion, profitability, or historical-source completeness.
