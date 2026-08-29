# Issue #22 Human Understanding

## Problem

Issue #21 created an atomic runtime status file, but the repository has no official read-only consumer for it.

Without one, every operator or supervisor must reimplement:

- freshness checks;
- retry vs fatal interpretation;
- schema validation;
- last-success age policy;
- safety-field validation.

That creates drift at exactly the point where a multi-day Stage 4 campaign needs dependable operational monitoring.

## Solution

Add a read-only `pcs-prediction shadow-runtime-health` command.

It reads the operational status file and returns:

- machine-readable JSON;
- a stable reason code;
- exit 0 when the configured operational policy passes;
- exit 2 when the status is stale, fatal, malformed, unreadable, or violates an optional last-success threshold.

## Important distinction

This command does **not** evaluate:

- campaign Evidence;
- Stage 4 campaign completeness;
- predictive skill;
- profitability;
- funded-execution readiness.

A fresh runtime can still have an incomplete or unprofitable campaign.

## Retry semantics

A fresh `cycle_error_retry` means the process is alive but degraded.

It is therefore not automatically treated as dead.

Operators that require periodic successful cycles can set a separate maximum last-success age.

## Safety

The checker never:

- writes the status file;
- opens the Shadow ledger;
- mutates ClickHouse or canonical storage;
- prints the status file path;
- prints raw filesystem or JSON parser errors;
- handles private keys or signing.

Safety fields in the status payload must remain false. Contradictory status content fails closed.
