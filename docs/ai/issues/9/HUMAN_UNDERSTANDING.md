# Issue #9 Human Understanding

## What

Each Stage 4 runtime result now says not only what happened, but how long each completed processing phase took.

## Why

Issue #7 and #8 removed obvious unnecessary work. Further optimization should be chosen from measured bottlenecks rather than assumptions.

## Important distinction

The performance timer is monotonic and is used only for durations.

The trading-research decision clock remains the existing UNIX timestamp path. Timing instrumentation cannot make a target eligible, move a cutoff, or change a deadline.

## Early exits

A source-warmup cycle does not pretend that target inference ran. A no-target cycle does not pretend that dataset construction ran. Missing phase keys therefore carry useful operational meaning.

## Target timing context

For target cycles the report additionally exposes:

- milliseconds from decision cutoff to recorded completion;
- milliseconds remaining to the submission-equivalent deadline.

These are wall-clock context values, not monotonic phase durations.

## Safety

No signer, wallet, funded stake or transaction broadcast capability is introduced.
