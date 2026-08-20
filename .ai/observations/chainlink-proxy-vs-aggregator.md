# Chainlink Proxy Identity and Event-Emitter Identity Are Distinct
Status: observation
Date: 2026-08-20
Source case: `../cases/2026-08-20-v0-7-research-readiness/`
Confidence: high

## Context

PancakeSwap Prediction exposes an `oracle()` address that is used to obtain the settlement price. For BNBUSD, that address is a Chainlink proxy. Historical/recent feature collection needs the actual Chainlink `AnswerUpdated` events that carry feed updates.

## Observation

Do not assume the address returned by Prediction `oracle()` is the address that emits `AnswerUpdated`. Treat the Prediction oracle proxy and the Chainlink underlying aggregator as separate identities.

Route proof also needs to match the temporal semantics of the campaign. There are now two deliberately distinct proof modes:

### Current-route / latest-state proof

For a recent window when only current state is available:

1. read the latest Prediction `oracle()` proxy;
2. read that proxy's latest `aggregator()` implementation;
3. capture a post-read head;
4. stateless-scan Prediction `NewOracle` from the window start through that head;
5. stateless-scan the proxy `AggregatorConfirmed` over the same range;
6. reject the route if either change event is present;
7. collect `AnswerUpdated` from the proven underlying aggregator, not the proxy.

This proves that today's route can be traced back to the requested start only when no relevant change exists through the observed head.

### Fixed-window proof

For a fixed historical/recent research window whose end-block state is still readable:

1. read Prediction `oracle()` at the exact window-end block;
2. read that proxy's `aggregator()` at the same block;
3. stateless-scan Prediction `NewOracle` only across `[from_block, to_block]`;
4. stateless-scan proxy `AggregatorConfirmed` only across `[from_block, to_block]`;
5. reject the route if either change event is present;
6. collect `AnswerUpdated` from the proven underlying aggregator inside the same source window.

A fixed-window proof must not expand to the present chain head every time it is re-run. Otherwise a constant historical campaign acquires an ever-growing RPC workload and can eventually fail because of time/rate limits unrelated to the source window itself. If the endpoint cannot read state at the window-end block, fail closed or use another source; do not silently substitute today's route for the historical route.

Both change-scan modes must remain stateless rather than using resumable collector checkpoints because a failed route proof must remain a failed proof on repeated execution.

## Evidence

- `evidence/recent-public-chainlink-smoke-2026-08-19.json` records a successful public two-hour BNBUSD source smoke with no archive-state requirement.
- The Prediction oracle proxy was `0x0567f2323251f0aab15c8dfb1967e4e8a7d42aee`.
- The proven underlying Chainlink aggregator was `0xa6e8fee84f9bd528ad71917c9ddbb1fd3214f280`.
- The two-hour latest-state proof found `new_oracle_events=0` and `aggregator_confirmed_events=0` from block `116844485` through post-read head `116982209`.
- Collecting from the underlying aggregator inserted 218 Chainlink `AnswerUpdated` events for the requested two-hour window; the same run inserted 910 Prediction events and produced deterministic replay evidence for 25 rounds.
- An earlier implementation queried `AnswerUpdated` at the proxy and produced zero Chainlink events despite reporting the route as collected. The current script rejects a requested Chainlink run unless real `AnswerUpdated` events are inserted.
- Commit `7fc3d330dccfcf545a952ddcf13a33449f09eed0` adds the fixed-window route proof while preserving the existing latest-state proof.
- Commit `e08cd28a228d4d4ae89848e3ad2b4c78e9a66bf3` switches the fixed Aug 18–19 bootstrap to the window-bound proof.
- Commit `6961af4965f73a6e36ed0e0233e7dabf887ec287` adds tests proving the fixed-window path does not read the current head and still rejects in-window `NewOracle` / `AggregatorConfirmed` changes.
- Normal PR CI run 919 passed with 309 tests, 87% coverage, Ruff/mypy/Bandit/pip-audit green, plus ClickHouse integration, Gitleaks, and the pinned legacy 144k-round audit.

The fixed Aug 18–19 one-day Chainlink source remains an empirical gate until its own current `last-success` evidence exists; the green software tests establish implementation behavior, not external-source success.

## Why it matters

Querying the proxy for feed-update events can silently yield an empty Chainlink feature stream while the Prediction side appears healthy. Separately, proving a fixed historical window by scanning from that window all the way to today's head makes source validation non-stationary and increasingly expensive. Tracking both identities and selecting the proof semantics that match the campaign turns the oracle route into an auditable, bounded, fail-closed source contract.

## Applicability

- recent PancakeSwap Prediction + Chainlink collection;
- fixed historical/recent source windows that can read route state at their end block;
- Chainlink EACAggregatorProxy-style feeds where events are emitted by an underlying aggregator;
- any source manifest or feature pipeline that needs to bind settlement oracle identity to the actual event-emitting contract.

## Exceptions / Limitations

Neither proof establishes the complete historical route timeline before its declared start. A window containing `NewOracle` or `AggregatorConfirmed` requires historical route reconstruction rather than assuming one stable route. The fixed-window method also requires the source to support state reads at the window-end block. Public endpoint capability and retention can change, so the real external-source gate must be re-run and recorded separately from unit/CI success.

## Related files

- `src/pancake_prediction/public_collector.py`
- `src/pancake_prediction/recent_bootstrap.py`
- `scripts/run_recent_public_bootstrap.py`
- `tests/test_public_collector.py`
- `.github/workflows/recent-public-chainlink-smoke.yml`
- `.github/workflows/recent-public-chainlink-day.yml`
- `evidence/recent-public-chainlink-smoke-2026-08-19.json`

## Related cases

- `../cases/2026-08-20-v0-7-research-readiness/`
