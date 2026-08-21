# Chainlink Proxy Identity and Event-Emitter Identity Are Distinct
Status: observation
Date: 2026-08-21
Source case: `../cases/2026-08-20-v0-7-research-readiness/`
Confidence: high

## Context

PancakeSwap Prediction exposes an `oracle()` address used for settlement. For BNBUSD, that address is a Chainlink proxy, while `AnswerUpdated` is emitted by the proxy's underlying aggregator. Recent research collection must bind both identities to the exact campaign window without pretending a public RPC has historical-state capabilities it does not have.

## Observation

Do not assume the address returned by Prediction `oracle()` emits `AnswerUpdated`. Treat the Prediction oracle proxy and Chainlink underlying aggregator as separate identities, and choose a route-proof method whose temporal and RPC-capability assumptions are explicit.

### Current-route / latest-state proof

A latest-state proof may read today's Prediction `oracle()` and Chainlink `aggregator()`, capture a post-read head, then stateless-scan `NewOracle` and `AggregatorConfirmed` backward to the requested start. This is valid only if the entire requested interval through the observed head is scanned with no route changes.

### Fixed window-end state proof is capability-dependent

A fixed campaign can in principle read `oracle()` and `aggregator()` at the exact window-end block and scan changes only inside the window. That keeps proof cost stationary, but it requires historical/recent state at that exact block.

The Aug 18–19 BNBUSD window empirically showed that this assumption does not hold across the current unauthenticated public routes used by the project:

- `https://rpc-bsc.48.club`: historical block-tagged `eth_call` returned `not supported`;
- `https://bsc-mainnet.public.blastapi.io`: GitHub runner received HTTP 403;
- `https://bsc-dataseed.bnbchain.org`: historical call failed with `missing trie node`.

Therefore the one-day public source must not claim that fixed window-end state is available.

### Persisted route anchor + backward change scan

A previously persisted successful later-window route proof can instead serve as a fixed anchor when all of the following hold:

1. the anchor evidence itself is successful and contains real Chainlink events;
2. its Prediction proxy and underlying aggregator identities agree across the proof and collected-event metadata;
3. its exact bytes are SHA-256 bound into the new proof;
4. the anchor block is at or after the source-window end;
5. Prediction `NewOracle` is statelessly scanned from the source start through the anchor block;
6. proxy `AggregatorConfirmed` is statelessly scanned over the same fixed interval;
7. either change event causes a fail-closed rejection.

This establishes that the later proven route extends backward across the requested source window without any historical `eth_call`, without reading the current head, and without making a fixed campaign's proof range grow over time.

Change scans must remain stateless rather than resumable; a failed route proof must remain failed on repeated execution.

## Evidence

The persisted anchor is `evidence/recent-public-chainlink-smoke-2026-08-19.json`:

- anchor block: `116844485`;
- Prediction oracle proxy: `0x0567f2323251f0aab15c8dfb1967e4e8a7d42aee`;
- Chainlink aggregator: `0xa6e8fee84f9bd528ad71917c9ddbb1fd3214f280`;
- 218 real `AnswerUpdated` events in the successful two-hour source run;
- anchor evidence SHA-256 used by the Aug 18 proof: `88991ebf1802fbcdd399f5bc477f19facdf60de3a2b582b1e39f14c1a16ca0e3`.

`evidence/recent-chainlink-route-proof-probe-last-success.json` records successful external run `32481332181` on `https://rpc-bsc.48.club`:

- source start: block `116556542`;
- source end: block `116748497`;
- proof through anchor: block `116844485`;
- `new_oracle_events=0`;
- `aggregator_confirmed_events=0`;
- `historical_state_required=false`;
- method `persisted_route_anchor_then_stateless_change_scan_backward_over_fixed_source_range`;
- artifact and semantic probe both succeeded.

The full one-day external collection is now independently proven by `evidence/recent-public-chainlink-2026-08-18-to-19-last-success.json`, source run `32481332419`:

- exact source range `116556542..116748497`;
- 14,496 canonical Prediction events and 8,673 bets;
- 282 `StartRound`, 282 `LockRound`, and 282 `EndRound` events;
- 2,615 real underlying-aggregator `AnswerUpdated` events;
- 284 deterministic replay rounds;
- exact anchor SHA-256 `88991ebf1802fbcdd399f5bc477f19facdf60de3a2b582b1e39f14c1a16ca0e3`;
- zero `NewOracle` and zero `AggregatorConfirmed` events through the fixed anchor block;
- `historical_state_required=false`;
- source artifact publication and source semantic gate both succeeded.

Implementation lineage:

- `ec50ca7b2e48ed5f85f3e7fb5dce37bc9693cbbc`: anchored stateless route proof;
- `a490b7d80260b3be420250b55668420844a1b7d1`: typed `ChainlinkRouteAnchor` and bootstrap integration;
- `10768c3f3ceda7608d268ac33bcfad5de31d01ec`: package-level exact-byte anchor evidence loader;
- `a831220e32173a78e879df4024e60f4ffcba6e19`: CLI uses the packaged anchor loader.

The one-day source success establishes source acquisition and route identity only. The downstream economic smoke is separately source-bound and persisted, and neither result by itself proves profitability.

## Why it matters

Querying the proxy for feed updates can silently produce an empty feature stream. Requiring unavailable historical state can also make a logically valid recent source permanently uncollectable. A SHA-bound later route anchor plus fixed backward change scans preserves route identity, bounded work, reproducibility, and fail-closed semantics without overstating public RPC capabilities.

## Applicability

- recent PancakeSwap Prediction + Chainlink collection;
- fixed recent windows where logs remain available but historical contract state does not;
- Chainlink proxy/aggregator architectures with explicit route-change events;
- source manifests that must bind an event emitter to a persisted route proof.

## Exceptions / Limitations

An anchored proof does not reconstruct a window containing a route change. If `NewOracle` or `AggregatorConfirmed` appears between the source start and anchor, the single-route proof is invalid and historical route reconstruction is required. It also does not establish source completeness outside the explicitly scanned interval, nor does it satisfy the deployment-era historical-source gate.

## Related files

- `src/pancake_prediction/public_collector.py`
- `src/pancake_prediction/recent_bootstrap.py`
- `src/pancake_prediction/chainlink_anchor.py`
- `scripts/run_recent_public_bootstrap.py`
- `tests/test_chainlink_route_anchor.py`
- `.github/workflows/recent-chainlink-route-proof-probe.yml`
- `.github/workflows/recent-public-chainlink-day.yml`
- `evidence/recent-public-chainlink-smoke-2026-08-19.json`
- `evidence/recent-chainlink-route-proof-probe-last-success.json`
- `evidence/recent-public-chainlink-2026-08-18-to-19-last-success.json`

## Related cases

- `../cases/2026-08-20-v0-7-research-readiness/`
