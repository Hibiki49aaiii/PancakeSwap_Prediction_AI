# Issue #7 Human Understanding

## What changed

Stage 4 runtime no longer needs to ask ClickHouse for feature-market data belonging to every replay epoch whenever one live target decision is open.

Instead, the inference layer first declares exactly which feature epochs the current decision can legally use. The ClickHouse dataset builder then loads only time chunks containing those epochs.

## Why this is safe

The optimization does not reduce the model training history arbitrarily.

The current model uses all rows that were already settled, outside the purge zone, and known before the target decision. Those exact epochs remain included.

Pool/history features are still derived from the full canonical replay, so filtering the expensive ClickHouse path does not erase prior-round context.

## What did not change

- model feature set
- model training algorithm
- calibration algorithm
- pool projection algorithm
- economic EV calculation
- purge boundary
- source lineage rules
- prospective live warmup
- deadline guard
- append-only Shadow Ledger
- no-signing/no-broadcast/no-funded-execution boundary

## Failure modes

The runtime still refuses to record a target when:

- live source warmup is incomplete;
- target feature row cannot be built;
- model history is insufficient;
- source provenance/route validation fails;
- ClickHouse schema is unsafe;
- final computation reaches the submission-equivalent deadline.

## Remaining optimization opportunity

Because the model intentionally uses all eligible historical training rows, this change cannot make runtime cost constant as history grows.

If measurements show the target window is still too tight, the next safe optimization is a source-bound persistent cache of immutable historical `ResearchFeatureRow` values, leaving only the live target row to be built during the decision window.
