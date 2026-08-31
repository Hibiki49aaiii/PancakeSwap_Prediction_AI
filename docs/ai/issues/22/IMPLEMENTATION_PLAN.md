# Issue #22 Implementation Plan

## Goal

Provide one official read-only consumer for Issue #21 operational status checkpoints so multi-day Stage 4 supervision does not require ad-hoc schema interpretation.

## Base

- branch: `agent/v0.7-alpha-research`
- base SHA: `743c2dd9bc4f43564702ab1ee717cada423ca46b`
- test suite: 499 tests / 87%
- exact-head CI #1385: success

## Architecture

Create `src/pancake_prediction/shadow_runtime_health.py`.

The module owns:

1. JSON file reading;
2. redacted invalid-file handling;
3. Issue #21 schema invariant validation;
4. wall-clock freshness evaluation;
5. optional last-success-age policy;
6. a typed immutable health report with `as_dict()`.

`src/pancake_prediction/cli.py` owns only:

- argument parsing;
- threshold validation;
- JSON printing;
- exit-code mapping.

Do not add consumer logic to `shadow_runtime_cli.py`; that file remains the producer.

## CLI

```bash
pcs-prediction shadow-runtime-health \
  --status-file artifacts/stage4-runtime-status.json \
  --max-status-age-seconds 30
```

Optional:

```bash
  --max-last-success-age-seconds 300
```

## Report semantics

The report includes:

- `check_passed`
- `operationally_alive`
- `degraded`
- `reason`
- `runtime_status`
- `fresh`
- `age_ms`
- `last_success_at_ms`
- `last_success_age_ms`
- `consecutive_cycle_errors`
- fixed safety fields
- `campaign_evidence_checked=false`

### Fresh success

- alive: true
- degraded: false
- check passed: true unless an explicitly stricter last-success policy fails

### Fresh retry

- alive: true
- degraded: true
- check passed: true by default
- optional last-success policy can fail the check while preserving alive=true

### Fatal

- alive: false
- degraded: true
- check passed: false

### Invalid/stale

- alive: false
- degraded: true
- check passed: false

## Validation boundary

Validate required invariants from the current writer only.

Allow unknown extra keys for forward compatibility.

Required common invariants:

- root JSON object
- known status
- non-negative integer timestamps/counters, rejecting bool-as-int
- `last_success_at_ms <= updated_at_ms`
- all four safety fields exactly false

Success invariants:

- non-empty `cycle_status`
- zero consecutive errors
- last-success equals update timestamp

Retry invariants:

- non-empty `error_type`
- positive max error budget
- `1 <= consecutive < max`
- positive finite retry interval

Fatal invariants:

- non-empty `error_type`
- positive max error budget
- consecutive errors >= 1

## Redaction

Do not surface:

- file path;
- `OSError` message;
- JSON decoder message;
- raw invalid payload contents;
- provider endpoint or credentials.

Use stable reason codes instead.

## Alternatives considered

### Parse status directly in shell/systemd

Rejected. It duplicates schema semantics outside the repository and invites drift.

### Put health logic in `shadow_runtime_cli.py`

Rejected. Producer and consumer responsibilities would be coupled and make the runtime CLI harder to reason about.

### Require retry to fail immediately

Rejected. It contradicts Issue #19/#20 bounded recovery semantics.

### Treat health as campaign Evidence

Rejected. Liveness and campaign proof are different domains.

## Risks and mitigations

- **Clock skew**: future `updated_at_ms` fails closed.
- **Schema evolution**: unknown keys are accepted.
- **Long retry loop hides lack of progress**: optional last-success threshold.
- **Status file corruption**: stable invalid reason, no raw parser output.
- **Monitoring side effects**: implementation is read-only and tests file content remains unchanged.

## Verification

Unit tests cover:

- fresh success;
- fresh retry;
- fatal;
- stale;
- future timestamp;
- malformed JSON;
- missing/unreadable status;
- safety contradiction;
- schema contradiction;
- unknown extra fields;
- optional last-success threshold;
- retry-before-first-success;
- read-only behavior.

CLI tests cover:

- JSON output and exit 0;
- failed check exit 2;
- threshold argument validation.

Then run:

- Ruff
- mypy strict
- pytest + coverage
- Bandit
- pip-audit
- ClickHouse integration
- Gitleaks
- pinned 144,000-round audit

## Pre-Implementation Review

### Architecture

The proposed module boundary preserves producer/consumer separation and keeps campaign semantics untouched.

### Failure/Security

All untrusted file failures collapse to stable reason codes. Safety-field contradictions and future timestamps fail closed.

### Human understanding

The checker answers an operational monitoring question only. A passing health check must never be presented as Stage 4 completion or profitability evidence.


# Implementation Result — 2026-08-29

Issue #22 implementation is complete.

## Implemented

- Added pure/read-only `src/pancake_prediction/shadow_runtime_health.py`.
- Added typed immutable `ShadowRuntimeHealthReport`.
- Added `pcs-prediction shadow-runtime-health`.
- Required operational freshness policy:
  - `--status-file`
  - `--max-status-age-seconds`
- Optional successful-cycle freshness policy:
  - `--max-last-success-age-seconds`
- Fresh `cycle_success`:
  - alive;
  - non-degraded;
  - exit 0.
- Fresh `cycle_error_retry`:
  - alive;
  - degraded;
  - exit 0 by default.
- Optional last-success threshold can fail a fresh retry check without falsely marking the process dead.
- `cycle_error_fatal`, stale status, future timestamp, malformed JSON, unreadable file, schema contradiction and safety contradiction fail closed with exit 2.
- Raw filesystem/JSON parser details and status paths are not emitted.
- Unknown extra JSON fields are accepted for forward compatibility.
- Status inspection is read-only and does not touch:
  - Shadow ledger;
  - canonical SQLite;
  - ClickHouse;
  - campaign manifest;
  - campaign Evidence.
- Added unit and CLI regression coverage.

## Implementation correction

Initial implementation commit `db5adb19d4c839b4a678245e3e83a865bcf58bf0` passed Ruff, pytest, Bandit and pip-audit, but mypy strict found a local variable name collision in `cli.main()`: the new health result reused the generic name `report`, conflicting with a later Shadow Ledger report assignment in the same function scope.

No runtime behavior change was required. The health variable was renamed to `health_report` in:

`9ec82d2ea5dc5d902ef85cb4f619c71b3533d75b`

This preserved the implementation while restoring strict type consistency.

## Verification

Production/test source SHA:

`9ec82d2ea5dc5d902ef85cb4f619c71b3533d75b`

Quality Evidence #325 / run `33257550925`:

- **516 tests passed**
- **87% coverage**
- Ruff success
- mypy strict success
- Bandit success
- pip-audit success
- final quality gate success

Full CI #1389 / run `33257552873`:

- test / coverage success
- ClickHouse integration success
- Gitleaks success
- pinned legacy **144,000-round** audit success
- overall CI success

Persisted source quality Evidence was updated automatically in:

`ba71f332deab4f7c7e59be88692fdd40f8f8e5c6`

## Post-Implementation Review

### Operational semantics

The checker distinguishes **process health** from **campaign validity**. A fresh success or retry can pass operational health while Stage 4 campaign Evidence remains incomplete.

### Retry behavior

A bounded retry is intentionally treated as alive-but-degraded rather than dead. Operators can independently require recent successful cycles through `--max-last-success-age-seconds`.

### Schema evolution

The checker validates safety- and liveness-critical invariants but permits unknown additional keys. This keeps current safety checks strict without freezing the entire status payload forever.

### Failure privacy

Untrusted file paths, raw JSON parser messages and filesystem exception messages are not propagated into the health report.

### Read-only boundary

The health path performs no storage initialization or mutation and never acquires runtime/source locks. It is safe to call from an external supervisor.

### Safety

No private key, signer, transaction signing, mainnet broadcast, funded execution, credential issuance/change, profitability promotion or full-history promotion was introduced.

### Remaining empirical boundary

This improves supervision of a long-running Stage 4 campaign; it does not complete that campaign. The real multi-day prospective campaign remains the next empirical milestone.
