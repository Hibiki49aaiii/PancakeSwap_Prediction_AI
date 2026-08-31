# Issue #21 Implementation Plan

## Goal

Add a durable atomic operational status checkpoint for long-running Stage 4 processes without conflating liveness with campaign Evidence.

## Current gap

Successful cycles can atomically update runtime/campaign Evidence, but retry status exists only on stdout and fatal cycle errors exist only on stderr.

A file-based supervisor therefore cannot distinguish:

- healthy process with recent success;
- process alive but retrying;
- process that terminated on a fatal error.

## CLI

Add optional:

`--status-output <path>`

Normal runtime only.

Reject path equality with every Evidence output.

Reject `--preflight-only --status-output`.

## Operational state

Track in the CLI process:

- `last_success_at_ms: int | None`;
- current consecutive cycle-error count.

Use a wall-clock millisecond timestamp at the moment the checkpoint is written.

### Success payload

- status: `cycle_success`
- cycle_status: report status
- updated_at_ms
- last_success_at_ms = updated_at_ms
- consecutive_cycle_errors = 0
- safety fields

### Retry payload

Extend/reuse retry telemetry with:

- updated_at_ms
- last_success_at_ms

The stdout retry JSON and status-file retry JSON should represent the same state.

### Fatal payload

Before terminal `parser.error()`:

- status: `cycle_error_fatal`
- error_type only
- updated_at_ms
- last_success_at_ms
- consecutive_cycle_errors
- max_consecutive_cycle_errors
- safety fields

No raw exception message.

Max-consecutive exhaustion also writes fatal status before exit.

## Atomic writer

Reuse the same temporary-sibling + `replace()` pattern used by Evidence writes.

Create a semantic wrapper/helper named for operational status so call sites do not imply that the status artifact is campaign Evidence.

An OSError while writing the status checkpoint is a fatal CLI error.

## Path validation

Generalize existing output-path collision validation to include `--status-output`.

## Tests

- once success writes atomic status;
- continuous retry writes status;
- retry before first success has null last-success;
- success then retry preserves last-success timestamp;
- fatal ValueError writes fatal status and exits;
- retry-budget exhaustion writes fatal status;
- status redacts raw error details;
- output path collisions rejected;
- preflight/status combination rejected;
- simulated status write failure exits;
- no temporary status file remains after successful writes.

## Semantic boundary

Status checkpoint path/content/time are operational only.

Do not add them to:

- `ShadowRuntimeConfig`;
- campaign manifest;
- campaign Evidence.

## Safety

No signer, broadcast, funded execution, credential issuance/change or profitability promotion.

## Base

- branch: `agent/v0.7-alpha-research`
- base SHA: `b888e03c04ae970f33841fc53d401f1741b5bae0`
- 491 tests / 87% / CI #1375 green.


# Implementation Result — 2026-08-29

Issue #21 implementation is complete.

## Implemented

- Added normal-runtime CLI option:
  - `--status-output <path>`.
- Status output is rejected with `--preflight-only`.
- Status path participates in the existing output-path collision guard and must differ from:
  - preflight output;
  - runtime cycle Evidence;
  - campaign latest Evidence;
  - campaign last-success Evidence.
- Added operational wall-clock helper and explicit payload builders for:
  - `cycle_success`;
  - `cycle_error_retry`;
  - `cycle_error_fatal`.
- Success status records:
  - completed cycle status;
  - `updated_at_ms`;
  - `last_success_at_ms`;
  - zero consecutive errors;
  - no-signing/no-broadcast/no-funding safety fields.
- Retry status now records:
  - exception class only;
  - `updated_at_ms`;
  - preserved `last_success_at_ms`;
  - retry counters and retry interval;
  - safety fields.
- Fatal status is written before:
  - immediate source/integrity/config failure;
  - one-shot cycle failure;
  - maximum consecutive retry exhaustion.
- Retry before any successful cycle records `last_success_at_ms=null`.
- Successful cycle resets the error counter and establishes a new last-success timestamp.
- Later retry preserves the prior successful timestamp.
- Status writes reuse the existing sibling-temporary + atomic replace implementation.
- Configured status-write `OSError` fails closed with type-only terminal output.
- Raw provider/filesystem exception messages are never stored in status.
- Status path/content/timestamps are excluded from:
  - `ShadowRuntimeConfig`;
  - campaign manifest;
  - campaign Evidence.

## Files Changed

- `.ai/index.md`
- `.ai/observations/runtime-liveness-status-is-operational-not-campaign-evidence.md`
- `docs/STAGE4_SHADOW.md`
- `docs/ai/issues/21/HUMAN_UNDERSTANDING.md`
- `docs/ai/issues/21/IMPLEMENTATION_PLAN.md`
- `src/pancake_prediction/shadow_runtime_cli.py`
- `tests/test_shadow_runtime_cli.py`

## Verification

Production/test source SHA:
`9a6abccc6180e23544eca675e9cb29a0d17632b7`

Quality Evidence #323 / run `33255775384`:

- pytest: **499 passed**
- coverage: **87%**
- Ruff: success
- mypy strict: success
- Bandit: success
- pip-audit: success
- final quality gate: success

Full PR CI #1380 / run `33255776590`:

- pytest: **499 passed in 20.89s**
- coverage: **87%**
- test: success
- ClickHouse integration: success
- Gitleaks: success
- pinned legacy **144,000-round** audit: success
- overall CI: success

## Post-Implementation Review

### Operational usefulness

A file-based supervisor can now distinguish a recent successful cycle, an alive-but-retrying runtime, and terminal cycle failure without parsing stdout/stderr history.

### Evidence boundary

The status artifact deliberately does not prove campaign validity, completeness or profitability. Campaign Evidence remains the authoritative campaign proof.

### Last-success semantics

The runtime never fabricates a successful timestamp. Before the first successful cycle it is null; after success it is preserved through later retry states.

### Atomicity

Status uses the same atomic replacement pattern as Evidence outputs, so readers cannot observe partially written JSON.

### Privacy

Error status contains only exception class names. Raw provider/filesystem errors are not serialized.

### Failure behavior

If the user explicitly configures a status output and it cannot be written, the process fails closed instead of silently losing the requested health channel.

### Safety

No private key, signer, transaction signing, mainnet broadcast, funded execution, credential issuance/change, profitability promotion or full-history promotion was introduced.

### Scope boundary

Service-manager configuration, distributed health checks, Prometheus metrics and alert delivery remain separate work.
