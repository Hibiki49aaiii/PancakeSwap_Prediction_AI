# Evidence

## Full historical archive gate

`evidence/archive-rpc-preflight.json` remains the authoritative redacted full-history gate and currently records `configured=false` / `archive_ready=false`. The archive workflow persists evidence and then explicitly fails when the probe is not ready; `historical-bootstrap` likewise fails rather than silently skipping when the prerequisite is absent.

## Public historical-source capability matrix

`evidence/public-archive-candidate-probe.json` demonstrates that provider/public/archive labels are insufficient:

- Blast public RPC passed the exact historical `eth_getCode` + `eth_call` state probe but did not prove historical log collection;
- `evidence/public-blast-bootstrap-smoke.json` then failed a real deployment-era log request, so Blast was not promoted to a complete historical source;
- `https://rpc-bsc.48.club` proved recent confirmed Prediction-log access while old creation-era history remained unavailable.

Historical state, historical logs, recent logs, and sustained collector throughput therefore remain independent capabilities.

## Recent Prediction collection

The pre-fix recent bootstrap accidentally used a genesis-to-head timestamp binary search and could request deep historical headers for a recent window. `src/pancake_prediction/recent_bootstrap.py` now finds a local lower bound by exponential backoff from confirmed head and binary-searches only inside that recent range.

`evidence/recent-public-bootstrap-smoke-2026-08-19-last-success.json` preserves the successful two-hour Aug 19 Prediction route.

After the RPC layer gained explicit pacing and HTTP 429 retry handling, `evidence/recent-public-bootstrap-2026-08-18-to-19.json` records a successful one-day BNBUSD public bootstrap on `https://rpc-bsc.48.club`:

- approximately 192k blocks scanned;
- 14,496 canonical Prediction events;
- 8,673 bets;
- 282 StartRound events;
- 282 LockRound events;
- 282 EndRound events;
- deterministic replay evidence for 284 rounds;
- no archive-state requirement;
- no signing/live broadcast path.

The public RPC policy used by the current source path is explicit and persisted by `scripts/run_recent_public_bootstrap.py`: 20 s timeout, 6 attempts, 1.5 s exponential-backoff base, and 0.15 s minimum request interval. HTTP 429 handling also honors numeric `Retry-After` when present. Canonical block-hash consistency checks were not removed to obtain this success.

## Recent Chainlink route

The first recent Chainlink attempt proved the Prediction oracle proxy was unchanged but queried `AnswerUpdated` at that proxy and inserted zero Chainlink events. That was rejected as data-completeness evidence.

The corrected route distinguishes the Prediction oracle proxy from its underlying Chainlink aggregator. The proof is stateless and rejects either Prediction `NewOracle` or Chainlink proxy `AggregatorConfirmed` changes over the requested window through a post-read head.

`evidence/recent-public-chainlink-smoke-2026-08-19-last-success.json` preserves a successful two-hour Aug 19 route:

- Prediction oracle proxy: `0x0567f2323251f0aab15c8dfb1967e4e8a7d42aee`;
- underlying Chainlink aggregator: `0xa6e8fee84f9bd528ad71917c9ddbb1fd3214f280`;
- `new_oracle_events=0`;
- `aggregator_confirmed_events=0`;
- 218 real Chainlink `AnswerUpdated` events inserted from the underlying aggregator;
- 910 Prediction events;
- deterministic replay evidence for 25 rounds.

One-day Prediction + Chainlink validation is intentionally a separate gate with `latest` and `last-success` evidence files. A transient failure must not erase the last proven success.

## Evidence persistence semantics

Recent smoke workflows now separate:

- `*-latest.json`: the most recent attempt, whether successful or not;
- `*-last-success.json`: updated only after a successful run.

This prevents transient provider 403/429 responses from overwriting the stable evidence referenced by External Intelligence.

## Current quality evidence

`evidence/quality-gate.json` for source SHA `b4514d85ac7ccd3505994d205404bfbf7e5cb198` records:

- `ready=true`;
- Ruff: success;
- mypy strict: success;
- pytest: 304 passed;
- total coverage: 87%;
- Bandit: success;
- pip-audit: success.

GitHub Actions run 867 also completed the normal PR CI successfully, including Gitleaks, ClickHouse integration, and the legacy 144k-round audit.

## Real Binance archive validation

`evidence/binance-real-sample-2026-08-01.json` remains valid parser/checksum/ClickHouse integration evidence for official BNBUSDT Spot and USD-M Futures archives. It is software/data-contract evidence, not profitability evidence.

## Repository-level invariants

The current status output, README, PR scope, and tests continue to enforce:

- no signing authority in research/model layers;
- no live broadcast path in the current stage;
- transaction-capable Stage 5 path restricted to loopback/local-fork;
- green software/data validation is not sufficient evidence of trading profitability.
