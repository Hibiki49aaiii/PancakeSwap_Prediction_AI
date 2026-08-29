# Runtime liveness status is operational, not campaign Evidence

Status: observation / high
Date: 2026-08-29

A long-running runtime needs durable liveness state, but that state must not be confused with evidence that the campaign itself is valid or complete.

## Separate questions

Campaign Evidence answers:

- what predictions/settlements exist;
- whether the campaign manifest and ledger chain are valid;
- whether campaign coverage gates are satisfied.

Operational status answers:

- whether the runtime recently completed a cycle;
- whether it is retrying;
- when it last succeeded;
- whether it terminated on a fatal cycle error.

These are different proof domains.

## Rule

A runtime status checkpoint should be:

- optional;
- atomically replaced;
- updated on success, retry and fatal cycle termination;
- redacted to exception class rather than raw exception text;
- excluded from campaign manifest identity and campaign Evidence.

## Last-success semantics

Preserve the timestamp of the last successfully completed cycle across later retry states.

Before the first success, last-success must be null rather than fabricated.

## Failure behavior

If the operator explicitly configured a status checkpoint and the runtime cannot write it, fail closed. Silent loss of the requested operational health channel defeats its purpose.

## Atomicity

File-based supervisors must never observe partial JSON.

Use a temporary sibling write followed by an atomic replace, and keep status paths distinct from Evidence output paths.

## Revalidate against

- `src/pancake_prediction/shadow_runtime_cli.py`
- Issue #21 tests
