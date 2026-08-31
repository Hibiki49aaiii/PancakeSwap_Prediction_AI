# Recent Bootstrap Must Keep Timestamp Header Search Local
Status: observation
Date: 2026-08-20
Source case: `../cases/2026-08-20-v0-7-research-readiness/`
Confidence: high

## Context

The public recent bootstrap is intentionally designed to collect recent canonical Prediction events without requiring archive contract state. Its timestamp-to-block resolver therefore must not introduce an unrelated dependency on arbitrary old block headers.

## Observation

A genesis-to-head binary search for a recent timestamp creates an accidental historical-header requirement: the first midpoint can be tens of millions of blocks older than the requested window. Public nodes that can serve current headers and recent logs may reject or prune those old midpoint headers, causing a recent bootstrap to fail before any recent log request is attempted.

For recent windows, find a lower search bound by exponential backoff from the confirmed head, then binary-search only inside that local bound.

## Evidence

- `evidence/public-archive-candidate-probe.json` records `https://rpc-bsc.48.club` as `recent_logs_ready=true` with 8 `StartRound` logs in the confirmed-head-minus-5,000-block probe, while old creation-era state/log requests return `header not found`.
- Before the resolver repair, the Aug 19 smoke reached the same endpoint but attempted block `58489512`, far outside the requested recent window, and failed with `block not found`; that stale failure remains recoverable from Git history rather than being kept as the current evidence file.
- Commit `6ad31bb4629468d45e98171b99d013970fa31c7d` changes `resolve_timestamp_block_range` to obtain a nearby lower bound by exponential backoff from the confirmed head before local binary search.
- Commit `8b42f3c002c43b9c80fa4f65fd1718dd603b3664` adds regression coverage proving a recent range near head 1023 never probes block 0 and does not probe below block 991.
- Current `evidence/recent-public-bootstrap-smoke-2026-08-19.json` is the post-fix end-to-end confirmation: the same public 48.club source successfully resolved blocks `116844485..116860482`, inserted 910 Prediction events including 559 bets, observed 23 Start/Lock/End events each, and produced deterministic replay evidence for 25 rounds. `success=true` and `workflow_outcome=success`.

## Why it matters

Without this distinction, a workflow labeled `archive_state_required=false` can still fail on providers solely because its block-number lookup accidentally demands deep historical headers. This both wastes viable recent-log sources and misclassifies provider capability. The post-fix smoke demonstrates that removing the accidental dependency can convert a previously rejected source into a working recent canonical Prediction-event source.

## Applicability

- timestamp-to-block resolution for recent BSC collection;
- public/full-node sources with bounded historical header/log retention;
- any future recent-window collector that claims not to need archive history.

## Exceptions / Limitations

A genuinely old requested timestamp can still require old headers and should fail if the provider cannot supply them. Exponential backoff does not make an endpoint archival; it only avoids probing history older than necessary for a recent target. The successful smoke collects Prediction events only (`chainlink_collected=false`) and therefore is not a substitute for the full historical Prediction + active-Chainlink source gate.

## Related files

- `src/pancake_prediction/recent_bootstrap.py`
- `tests/test_recent_bootstrap.py`
- `scripts/run_recent_public_bootstrap.py`
- `evidence/public-archive-candidate-probe.json`
- `evidence/recent-public-bootstrap-smoke-2026-08-19.json`

## Related cases

- `../cases/2026-08-20-v0-7-research-readiness/`
