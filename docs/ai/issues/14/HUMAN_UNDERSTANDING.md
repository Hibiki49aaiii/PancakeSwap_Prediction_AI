# Issue #14 Human Understanding

## Problem

The Shadow Ledger itself is transaction-safe, but a Stage 4 runtime cycle does much more than one SQLite write.

If two runtime processes point at the same Shadow DB, both can synchronize sources and calculate a prediction before the ledger transaction decides which append wins.

## Solution

Normal runtime holds one exclusive coordination lock for the entire process lifetime.

The lock is stored in a separate SQLite database derived from the Shadow DB path. The file can remain after a process exits; only the live SQLite exclusive transaction means the campaign is locked.

## Why not put the lock in the Shadow Ledger?

Holding an exclusive transaction on the real Shadow Ledger would block the runtime's own later connections for reconciliation/audit/append.

A separate coordination DB avoids coupling process coordination to append-only campaign data.

## Why not use a PID file?

A stale PID file survives crashes and PID reuse can make stale ownership ambiguous.

SQLite releases its live file lock when the connection/process disappears.

## Preflight

Preflight remains read-only and does not create/acquire the runtime coordination lock.

## Scope boundary

This protects one Shadow DB from multiple local runtime processes. It is not a Redis/etcd distributed lease and does not globally lock canonical BSC or ClickHouse sources.
