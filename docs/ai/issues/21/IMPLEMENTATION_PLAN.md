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
