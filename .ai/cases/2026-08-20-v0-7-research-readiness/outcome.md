# Outcome

## Current outcome

The v0.7 research foundation remains intact and all research/signing safety boundaries remain fail-closed.

The work has partially unblocked data acquisition without pretending the full historical gate is solved:

- a public two-hour recent BNBUSD Prediction route is proven;
- a public two-hour recent Prediction + Chainlink route is proven without archive-state reads;
- the Chainlink route now binds both the Prediction oracle proxy and its underlying aggregator, rejects route changes, and collects real `AnswerUpdated` events from the aggregator;
- the complete deployment-era historical Prediction + Chainlink campaign is still blocked until a source proves the full historical workload;
- the larger one-day public route is still subject to provider rate limits and is not promoted as reliable.

## Verification state

Validated source SHA: `594ed3c44ae5fb251b54456f4fae59c7ae0b4032`.

GitHub Actions CI run 856:

- Ruff: success;
- mypy strict: success;
- pytest: 300 passed;
- coverage: 87%;
- Bandit: success, 0 issues;
- pip-audit: success;
- Gitleaks: success;
- ClickHouse integration: success;
- legacy 144k-round audit: success;
- final quality-gate enforcement: success.

`evidence/quality-gate.json` is `ready=true` for the same source SHA.

Real public-source evidence:

- `evidence/recent-public-bootstrap-smoke-2026-08-19.json`: recent Prediction canonical collection succeeded;
- `evidence/recent-public-chainlink-smoke-2026-08-19.json`: recent Prediction + Chainlink collection succeeded, including 218 `AnswerUpdated` events from the proven underlying aggregator.

## Remaining risk

- `BSC_ARCHIVE_RPC_URL` is still not configured; complete historical reconstruction remains blocked on a source that proves the required historical state and sustained canonical Prediction/Chainlink collection workload.
- Public endpoint capability, retention, and rate limits can change; the successful two-hour source must be re-probed for later windows.
- The one-day public bootstrap currently fails under public-provider limits, so the two-hour success must not be generalized to arbitrary historical duration.
- A window containing Prediction `NewOracle` or Chainlink proxy `AggregatorConfirmed` cannot use the latest-state shortcut; it requires route reconstruction across the change.
- Recent source success and green CI are not profitability evidence.

## Follow-up

Near-term work can now proceed on two tracks without conflating their evidence:

1. Use the proven recent Prediction + Chainlink route for bounded recent dataset/feature validation and to continue improving data-source contracts.
2. Keep the full historical gate fail-closed while obtaining/configuring an authenticated archive-capable source or independently proving an alternative complete historical source.

After the complete historical gate passes:

1. run the prepared BNBUSD historical bootstrap;
2. bind the exact canonical Prediction/oracle timeline and Binance source window;
3. ingest checksum-verified Spot/Perp data under realistic positive availability-lag scenarios;
4. run source-bound `campaign-evaluate` with explicit stake/gas/inclusion latency;
5. compare sensitivity/ablation/regime stability;
6. progress to shadow/local-fork gates only when their own evidence criteria pass.

## Reusable knowledge

- Historical source readiness is a capability matrix, not a provider label or one-method success.
- Recent timestamp resolution must not accidentally introduce deep historical-header dependencies.
- Prediction oracle proxy identity and Chainlink event-emitter identity are distinct; prove both route layers stable before collecting `AnswerUpdated`.
- Evidence-preserving CI gates must retain failure semantics and revision identity.
- Successful software/data collection is not profitability evidence.
- Keep research authority separate from signing/live broadcast.
