# Issue #15 Human Understanding

## Problem

The canonical collector stores "how far collection has completed" in ordinary string metadata.

If two collectors share the same canonical DB, the one that finishes later can accidentally overwrite a newer block checkpoint with an older block number.

## Why this matters

The event rows themselves are mostly retry-safe, but the checkpoint controls where later collection resumes and what preflight considers the canonical collected head.

Progress metadata moving backwards makes the source look less advanced than it actually is and forces unnecessary replay.

## Correct model

These integer fields are high-water marks.

Once a collector has proven completion through block 120, a later writer that only proves through block 100 must not reduce the stored checkpoint.

## Reorg handling still works

Recent reorgs are handled by intentionally replaying `reorg_lookback` blocks before the high-water mark.

So the checkpoint remains monotonic while the read window still moves backward enough to detect recent chain changes.

## Scope

Only integer progress metadata gets monotonic semantics.

Generic metadata such as oracle-route proof strings remains ordinary replaceable metadata.
