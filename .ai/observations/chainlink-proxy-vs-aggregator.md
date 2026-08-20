# Chainlink Proxy Identity and Event-Emitter Identity Are Distinct
Status: observation
Date: 2026-08-20
Source case: `../cases/2026-08-20-v0-7-research-readiness/`
Confidence: high

## Context

PancakeSwap Prediction exposes an `oracle()` address that is used to obtain the settlement price. For BNBUSD, that address is a Chainlink proxy. Historical/recent feature collection needs the actual Chainlink `AnswerUpdated` events that carry feed updates.

## Observation

Do not assume the address returned by Prediction `oracle()` is the address that emits `AnswerUpdated`. Treat the Prediction oracle proxy and the Chainlink underlying aggregator as separate identities.

For a recent window without archive-state access, a fail-closed route is:

1. read the latest Prediction `oracle()` proxy;
2. read that proxy's latest `aggregator()` implementation;
3. capture a post-read head;
4. stateless-scan Prediction `NewOracle` from the window start through that head;
5. stateless-scan the proxy `AggregatorConfirmed` over the same range;
6. reject the route if either change event is present;
7. collect `AnswerUpdated` from the proven underlying aggregator, not the proxy.

The change scans must not use resumable collector checkpoints because a failed proof must remain a failed proof on repeated execution.

## Evidence

- `evidence/recent-public-chainlink-smoke-2026-08-19.json` records a successful public two-hour BNBUSD source smoke with no archive-state requirement.
- The Prediction oracle proxy was `0x0567f2323251f0aab15c8dfb1967e4e8a7d42aee`.
- The proven underlying Chainlink aggregator was `0xa6e8fee84f9bd528ad71917c9ddbb1fd3214f280`.
- The stateless proof found `new_oracle_events=0` and `aggregator_confirmed_events=0` from block `116844485` through post-read head `116982209`.
- Collecting from the underlying aggregator inserted 218 Chainlink `AnswerUpdated` events for the requested two-hour window; the same run inserted 910 Prediction events and produced deterministic replay evidence for 25 rounds.
- An earlier implementation queried `AnswerUpdated` at the proxy and produced zero Chainlink events despite reporting the route as collected. The current script now rejects a requested Chainlink run unless real `AnswerUpdated` events are inserted.
- `tests/test_public_collector.py` covers stable proxy+aggregator proof, Prediction `NewOracle` rejection, Chainlink `AggregatorConfirmed` rejection, a post-read race, repeated failed proof behavior, and insertion from the underlying aggregator.
- GitHub Actions CI run 856 validates source commit `594ed3c44ae5fb251b54456f4fae59c7ae0b4032`; `evidence/quality-gate.json` records 300 passed tests, 87% coverage, and `ready=true`.

## Why it matters

Querying the proxy for feed-update events can silently yield an empty Chainlink feature stream while the Prediction side appears healthy. That can create false data-completeness claims and distort feature availability. Tracking both identities and their change events turns the route into an auditable, fail-closed source contract.

## Applicability

- recent PancakeSwap Prediction + Chainlink collection;
- Chainlink EACAggregatorProxy-style feeds where events are emitted by an underlying aggregator;
- any source manifest or feature pipeline that needs to bind settlement oracle identity to the actual event-emitting contract.

## Exceptions / Limitations

The no-change proof only applies to the explicitly scanned window and post-read head. It does not establish the complete historical oracle/aggregator timeline before the window start. A window containing `NewOracle` or `AggregatorConfirmed` requires historical route reconstruction rather than inference from latest state. Public endpoint capability and retention can also change, so the real smoke must be re-run for later windows.

## Related files

- `src/pancake_prediction/public_collector.py`
- `src/pancake_prediction/recent_bootstrap.py`
- `scripts/run_recent_public_bootstrap.py`
- `tests/test_public_collector.py`
- `.github/workflows/recent-public-chainlink-smoke.yml`
- `evidence/recent-public-chainlink-smoke-2026-08-19.json`

## Related cases

- `../cases/2026-08-20-v0-7-research-readiness/`
