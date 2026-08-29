# Issue #20 Human Understanding

## Problem

A retry helps only if repeating the same operation later can reasonably succeed.

Network/provider failures can recover. A source-integrity contradiction usually cannot.

Before this issue, continuous Stage 4 used the same bounded retry for both.

Examples that should not be retried:

- the live oracle route no longer matches the campaign anchor;
- prospective Binance lineage was contaminated by a non-live source;
- the ClickHouse schema is structurally incompatible;
- source/config data is malformed.

## Rule

Retry only errors that still represent a potentially recoverable external operation.

Fail immediately when the runtime has evidence that its campaign/source assumptions are invalid.

## Why dedicated subclasses

Checking exception messages would make safety depend on wording.

Dedicated exception classes make the boundary explicit and testable while preserving the existing base exception types for callers.

## Privacy

Neither retry nor fatal terminal output should expose raw provider messages. The class name is enough for operators to distinguish the failure family without leaking endpoints or credentials.

## Outcome

A transient outage can recover without stopping a multi-day campaign, but a source-integrity contradiction cannot silently consume the retry budget before failing.
