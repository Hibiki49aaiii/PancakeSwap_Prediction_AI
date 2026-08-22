# Recent header search must distinguish overshoot from provider retention

Status: observation
Confidence: high
Observed: 2026-08-22
Related source run: `32503882364`
Related audit commit: `9abde60af8519eabe180a19fed6ebae8365f31ae`
Retention fix commit: `15b657cc418fec1e8b7cc6b32dcb0bc4f399581a`
Retention regression-test commit: `2488c2aaa7eef299e6e43375f3f9a7e04841c379`
Structured source-failure commit: `d8b98fa3e88df04a2a3bc246ed2eae7975c4777e`
Structured eth_getLogs diagnostic commit: `08f762e7f84f3ab070b9db4cea9d270cf8647d10`
Log-diagnostic regression-test commit: `052f6c959d7376d984039585d328af39ac42c31d`

## Observation

A head-local exponential timestamp-to-block search can still fail incorrectly on a pruned recent-header provider if its next doubled probe overshoots past the provider's retention boundary before the requested timestamp has been resolved.

For the Aug 16–19 BNBUSD source attempt, the exact requested start block is `116172651` at timestamp `1786838400`. The failed 48.club attempt had already read block `116216724`, whose timestamp is 2026-08-16 05:30:39 UTC and is therefore later than the requested start. The next exponential step jumped to `115168148`, where the provider returned `block not found`.

The latter error proves that the old search crossed the provider retention boundary; it does not by itself prove that the requested start block was unavailable at that run time. Failing the endpoint immediately at the overshot probe therefore conflated a search-algorithm defect with provider retention.

## Required behavior

When an exponential header probe returns the exact null-header / `block not found` condition while a newer candidate is already known to be readable:

1. treat only that specific missing-header condition as a retention-boundary candidate;
2. binary-search between the known unavailable block and the known available block for the first available header;
3. if the first available header timestamp is at or before the requested timestamp, use that block as the lower bound for the normal timestamp binary search;
4. if the first available header timestamp is later than the requested timestamp, fail closed as `PROVIDER_RETENTION` and preserve the requested timestamp, first available block/timestamp, and last unavailable block;
5. propagate unrelated RPC/HTTP failures unchanged rather than reclassifying them as retention.

This behavior does not make an under-retained provider acceptable; it separates an unnecessary overshoot from genuine inability to serve the requested window.

## Independent provider-limit finding

The same independent audit showed that the BNB public dataseed `eth_getLogs` error `-32005: limit exceeded` is a separate provider capability boundary. It reproduces at a single block with a single topic for Prediction `NewOracle`, proxy `AggregatorConfirmed`, and aggregator `AnswerUpdated`, including current blocks. Therefore range halving and topic partitioning cannot recover that endpoint at the minimum query unit. Existing singleton fail-closed behavior is correct and must not be weakened.

The RPC layer now preserves the exact failing `eth_getLogs` address, block range, topic set, JSON-RPC code, and whether the collector reached a single-block query. A single-block `-32005` is classified as `PROVIDER_LOG_LIMIT`; larger failing ranges remain split-eligible and are not prematurely classified as the terminal provider limit.

## Evidence boundary

- A corrected retention-aware resolver is software correctness evidence, not evidence that a provider retains the requested source window.
- A structured `PROVIDER_RETENTION` result is a successful diagnosis of an unavailable source, not source-gate success.
- A singleton `PROVIDER_LOG_LIMIT` is an external capability failure, not permission to skip route-change or event-completeness checks.
- None of these findings provide profitability evidence or alter signing/live-broadcast safety boundaries.
