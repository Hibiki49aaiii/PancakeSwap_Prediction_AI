# Runtime coordination locks are operational, not campaign semantics

Status: observation / high
Date: 2026-08-29

A long-running Stage 4 runtime needs a single-writer process guard that covers the entire cycle, not only the final SQLite append transaction.

## Why ledger transactions are insufficient

`BEGIN IMMEDIATE` on the Shadow Ledger protects one append transaction, but a runtime cycle performs source synchronization, canonical reads, settlement reconciliation, target selection, feature construction and inference before that append.

Two processes can therefore produce overlapping source writes and different observation-time predictions even if the ledger eventually rejects a conflicting append.

## Rule

For one Shadow campaign:

- acquire a campaign-scoped runtime coordination lock before source synchronization;
- keep it for the complete `--once` cycle or continuous loop lifetime;
- make acquisition non-blocking so accidental duplicate processes fail closed immediately;
- release on normal exit, exceptions and process termination;
- do not infer live ownership from a stale lock file alone.

## Separate coordination from campaign state

The process lock is operational coordination only.

Do not bind its path, file contents, PID, or current lock-held state into:

- the immutable campaign manifest;
- campaign Evidence;
- profitability or historical-completeness gates.

Those artifacts must describe decision semantics and proven campaign history, not the incidental process-management mechanism.

## SQLite coordination pattern

A separate SQLite lock database is useful when the actual Shadow Ledger opens its own connections throughout the cycle.

Holding an exclusive transaction on the real ledger would block the runtime from using that ledger itself.

A dedicated coordination DB with a live `BEGIN EXCLUSIVE` transaction lets SQLite/OS release ownership automatically when the connection or process disappears. The persistent lock DB file is not proof of a live owner.

## Preflight boundary

Read-only preflight should not acquire or create the runtime coordination lock. Preflight validates structural compatibility; normal runtime process ownership is acquired only when actual mutating operation begins.

## Revalidate against

- `src/pancake_prediction/shadow_runtime_lock.py`
- `src/pancake_prediction/shadow_runtime_cli.py`
- Issue #14 tests
