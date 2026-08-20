# Evidence

## Full historical archive gate

`evidence/archive-rpc-preflight.json` currently records:

- `configured: false`
- `archive_ready: false`
- the required repository secret is not configured

The archive preflight persists only redacted readiness evidence and now explicitly fails the workflow when the underlying probe is not successful. `historical-bootstrap` likewise fails explicitly when the archive prerequisite is absent rather than skipping substantive work and appearing green.

## Public historical-source capability matrix

`evidence/public-archive-candidate-probe.json` demonstrates that source capability is method- and workload-specific:

- the unauthenticated Blast public endpoint passed the exact historical `eth_getCode` + `eth_call` state probe;
- the same route did not prove historical log collection;
- `evidence/public-blast-bootstrap-smoke.json` then failed a real deployment-era one-block `eth_getLogs` request after retries, so Blast was not promoted to a complete historical source;
- `https://rpc-bsc.48.club` proved recent confirmed Prediction-log access while old creation-era requests were unavailable.

This supersedes the older statement that no tested public endpoint had any archive-state capability. What remains unproven is a public endpoint that satisfies the complete historical state + canonical Prediction/Chainlink collection workload.

## Recent Prediction collection

The pre-fix recent bootstrap used a genesis-to-head binary search to resolve a recent timestamp, accidentally probing deep historical block headers. This failed on public nodes that could serve recent logs.

After `src/pancake_prediction/recent_bootstrap.py` was changed to find a nearby lower bound by exponential backoff from the confirmed head and binary-search only inside that range, `evidence/recent-public-bootstrap-smoke-2026-08-19.json` succeeded on `https://rpc-bsc.48.club` for 2026-08-19 12:00–14:00 UTC:

- blocks `116844485..116860482`;
- 910 canonical Prediction events inserted;
- 559 bets;
- 23 StartRound, 23 LockRound, 23 EndRound events;
- zero reorgs and zero duplicate canonical heights;
- deterministic replay evidence for 25 rounds.

The larger one-day public bootstrap evidence remains unsuccessful because public provider rate limits interrupt that workload. The two-hour route therefore proves recent-source viability, not unlimited public retention/throughput.

## Recent Chainlink route

The first recent Chainlink attempt proved the Prediction oracle proxy was unchanged but queried `AnswerUpdated` at that proxy and inserted zero Chainlink events. That result was not accepted as data-completeness evidence.

The current route separates the Prediction oracle proxy from the event-emitting Chainlink aggregator. `evidence/recent-public-chainlink-smoke-2026-08-19.json` now records a successful two-hour BNBUSD run on `https://rpc-bsc.48.club`:

- `archive_state_required=false`;
- Prediction oracle proxy: `0x0567f2323251f0aab15c8dfb1967e4e8a7d42aee`;
- underlying Chainlink aggregator: `0xa6e8fee84f9bd528ad71917c9ddbb1fd3214f280`;
- proof range: block `116844485` through post-read head `116982209`;
- `new_oracle_events=0`;
- `aggregator_confirmed_events=0`;
- 218 Chainlink `AnswerUpdated` events inserted from the underlying aggregator;
- 910 Prediction events inserted over the requested collection window;
- deterministic replay evidence for 25 rounds;
- `chainlink_collected=true` and workflow outcome success.

The proof is stateless: it does not reuse collector checkpoints, so a previous failed stability proof cannot cause a later run to skip the change event that caused the failure.

## Current quality evidence

GitHub Actions CI run 856 completed successfully on source SHA `594ed3c44ae5fb251b54456f4fae59c7ae0b4032`:

- installed CLI smoke checks: success;
- Ruff: success;
- mypy strict: success;
- pytest: 300 passed;
- total coverage: 87%;
- Bandit: 0 issues identified;
- pip-audit: no known dependency vulnerabilities reported;
- Gitleaks: success;
- ClickHouse integration: success;
- legacy 144k-round audit: success;
- final quality-gate enforcement: success.

`evidence/quality-gate.json` records the same source SHA with `ready=true`, 300 passed tests, and all five source quality checks successful.

A transient failing CI immediately before this success was caused only by the newly added unit test querying SQLite column `address` instead of the actual `contract_address`; production collection had already inserted one fake `AnswerUpdated` correctly. Commit `594ed3c44ae5fb251b54456f4fae59c7ae0b4032` corrected the test assertion and the complete suite passed.

## Real Binance archive validation

`evidence/binance-real-sample-2026-08-01.json` remains valid evidence that the official BNBUSDT Spot and USD-M Futures archives pass checksum/parser/ClickHouse ingestion validation. This remains software/data-contract evidence only and is not profitability evidence.

## Repository-level invariants

The current status output, README, PR scope, and tests continue to enforce:

- no signing authority in research/model layers;
- no live broadcast path in the current stage;
- transaction-capable Stage 5 path restricted to loopback/local-fork;
- green software/data validation is not sufficient evidence of trading profitability.
