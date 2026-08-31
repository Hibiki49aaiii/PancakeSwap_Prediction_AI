# SQLite WAL File Hash Is Not a Logical Ledger Proof

Status: observation
Date: 2026-08-29
Source issue: `#4`
Confidence: high

## Context

The Stage 4 Shadow Ledger uses SQLite with `PRAGMA journal_mode=WAL`. Campaign Evidence historically included a SHA-256 of the SQLite main database file.

## Observation

When SQLite WAL mode is active, the SHA-256 of the main database file must not be treated as the sole proof of the current logical ledger state. Committed state may be represented through WAL/checkpoint behavior, and a physical-file digest describes bytes, not the append-only event-chain semantics.

For Shadow campaign Evidence, the authoritative logical binding is:

- ledger `event_count`;
- ledger hash-chain `head_digest`;
- derived `campaign_digest`.

A main-database-file SHA may still be retained as a physical snapshot identifier, but its scope must be explicit.

Do not force a WAL checkpoint merely to make a physical hash look authoritative unless checkpoint behavior is itself part of the evidence protocol and concurrency model.

## Why it matters

Evidence consumers need to distinguish byte-level file identity from logical append-only state identity. Treating them as interchangeable can bind metrics to the wrong logical ledger state or encourage unsafe checkpoint side effects.

## Applicability

- SQLite WAL-backed evidence stores;
- append-only ledgers;
- reproducibility manifests that combine database files and logical state digests;
- long-running processes with separate evidence checkpoint files.

## Related files

- `src/pancake_prediction/shadow_ledger.py`
- `src/pancake_prediction/shadow_campaign.py`
- `docs/STAGE4_SHADOW.md`
- `docs/ai/issues/4/IMPLEMENTATION_PLAN.md`
