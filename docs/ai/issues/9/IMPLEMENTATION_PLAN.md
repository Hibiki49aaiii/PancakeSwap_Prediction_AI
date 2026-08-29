# Issue #9 Implementation Plan

## Goal

Stage 4 continuous Shadow runtime の各 cycle で、どの phase が decision-window latency を消費したかを Evidence に残す。

## Clock separation

Runtime decisions and performance measurement use different clocks for different purposes.

- UNIX wall clock: existing decision timestamp / latest submission / completion semantics.
- `time.perf_counter_ns()`: process phase duration measurement only.

The monotonic clock must never replace the wall clock in target eligibility or deadline decisions.

## Timing payload

`ShadowRuntimeCycleReport.as_dict()` exposes:

```text
timing.clock = monotonic_perf_counter
timing.phase_durations_ms
timing.total_duration_ms
timing.decision_to_completion_ms
timing.submission_margin_ms
```

Phase durations are integer milliseconds.

Target-relative wall-clock context is included only when a target exists:

- `decision_to_completion_ms = completion - target decision`
- `submission_margin_ms = latest submission - completion`

A zero/negative submission margin indicates the submission-equivalent deadline has been reached or missed.

## Instrumented phases

- schema_check
- chain_sync
- binance_spot_sync
- binance_perp_sync (only when enabled)
- live_coverage
- canonical_input_load
- shadow_ledger_init
- settlement_reconciliation
- source_warmup_check
- target_selection
- required_epoch_plan
- dataset_build
- inference
- deadline_check
- ledger_append
- campaign_audit

Early-exit reports contain only phases that actually ran.

## Failure semantics

Hard source/integrity exceptions remain exceptions; no synthetic successful timing report is generated for a failed source-integrity cycle.

Operational non-fatal statuses retain timing Evidence:

- source_warmup
- no_eligible_target
- target_not_ready
- missed_submission_deadline
- prediction_recorded

## Verification

Tests require:

- all recorded phase durations are non-negative;
- total duration is non-negative and covers recorded phases;
- source_warmup contains no target/dataset/inference phases;
- no-target contains target-selection but no dataset/inference;
- target_not_ready includes dataset/inference but no deadline/ledger append;
- missed deadline includes deadline check but no ledger append;
- successful prediction includes full target path and campaign audit;
- decision-to-completion / submission margin match deterministic wall-clock overrides;
- safety flags remain unchanged.

## Out of scope

- adaptive tuning
- persistent feature cache
- model prewarm
- runtime scheduler changes
- alerting backend
- funded execution
