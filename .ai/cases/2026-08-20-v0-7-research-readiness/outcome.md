# Outcome

## Current outcome

The v0.7 research foundation remains intact and all research/signing safety boundaries remain fail-closed.

Data acquisition is now materially less blocked than at the start of this case:

- a public two-hour recent BNBUSD Prediction route is proven;
- a public one-day recent BNBUSD Prediction route is proven after adding paced/retry-aware RPC behavior;
- a public two-hour recent Prediction + Chainlink route is proven without archive-state reads;
- the Chainlink route binds both the Prediction oracle proxy and its underlying aggregator, rejects route changes, and collects real `AnswerUpdated` events from the aggregator;
- the complete deployment-era historical Prediction + Chainlink campaign is still blocked until a source proves the full historical workload;
- one-day Prediction + Chainlink remains an independent empirical gate and must not be inferred from shorter success.

## Verification state

Current paced public-RPC source revision: `b4514d85ac7ccd3505994d205404bfbf7e5cb198`.

GitHub Actions run 867 completed successfully for that revision. `evidence/quality-gate.json` records:

- Ruff: success;
- mypy strict: success;
- pytest: 304 passed;
- coverage: 87%;
- Bandit: success;
- pip-audit: success;
- `ready=true`.

Normal PR CI additionally passed Gitleaks, ClickHouse integration, and the legacy 144k-round audit.

Real public-source evidence:

- `evidence/recent-public-bootstrap-smoke-2026-08-19-last-success.json`: bounded recent Prediction collection succeeded;
- `evidence/recent-public-bootstrap-2026-08-18-to-19.json`: one-day Prediction collection succeeded with 14,496 events, 8,673 bets, and 284 replay rounds;
- `evidence/recent-public-chainlink-smoke-2026-08-19-last-success.json`: bounded recent Prediction + Chainlink collection succeeded, including 218 `AnswerUpdated` events from the proven underlying aggregator.

## Remaining risk

- `BSC_ARCHIVE_RPC_URL` is still not configured; complete historical reconstruction remains blocked on a source that proves required historical state plus sustained canonical Prediction/Chainlink collection.
- Public endpoint capability, retention, and rate limits can change. Successful bounded/current routes must be re-probed for later windows.
- One-day Prediction success does not establish one-day Chainlink success; that route has its own latest/last-success evidence.
- A window containing Prediction `NewOracle` or Chainlink proxy `AggregatorConfirmed` cannot use the latest-state shortcut; it requires historical route reconstruction across the change.
- Recent source success and green CI are not profitability evidence.

## Follow-up

Work can proceed on two explicit evidence tracks:

1. Use the proven recent Prediction / bounded Prediction+Chainlink paths for recent dataset and feature-pipeline validation, extending duration only when the corresponding empirical source gate passes.
2. Keep the full historical gate fail-closed while obtaining/configuring an authenticated archive-capable source or independently proving an alternative complete historical source.

Once a full historical source passes:

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
- Public RPC pacing and 429-aware retries can improve sustained recent collection without weakening canonical checks, but duration-specific success still requires empirical evidence.
- Latest-attempt evidence and last-success evidence must be separate when external providers are transient.
- Evidence-preserving CI gates must retain failure semantics and revision identity.
- Successful software/data collection is not profitability evidence.
- Keep research authority separate from signing/live broadcast.
