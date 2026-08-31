# Outcome

## Current outcome

The v0.7 research foundation is intact and all research/signing safety boundaries remain fail-closed.

The original recent-source readiness problem is now empirically resolved for the bounded Aug 18–19 BNBUSD campaign, while the complete deployment-era historical campaign remains deliberately blocked.

Proven gates now include:

- one-day canonical Prediction + Chainlink source;
- SHA-bound persisted Chainlink route anchor with fixed backward route-change scans;
- source-bound one-day economic OOS plumbing;
- eight-scenario economic sensitivity plus five-variant feature-family ablation;
- observed Stage 5B loopback/local-fork execution-readiness.

The Aug 16–19 recent source gate is authenticated-only and remains blocked until an authenticated BSC RPC secret is configured; the entire downstream OOS/robustness/cross-window comparison path is already implemented.

## Verification state

Code CI run `33184259413` (#1082) completed successfully with 333 tests passed. Ruff, mypy strict, Bandit, pip-audit, ClickHouse integration, secrets scan, legacy-round audit, and the quality gate all passed.

### One-day Prediction + Chainlink source

`evidence/recent-public-chainlink-2026-08-18-to-19-last-success.json` records run `32481332419`:

- endpoint `https://rpc-bsc.48.club`;
- blocks `116556542..116748497`;
- 14,496 canonical Prediction events;
- 8,673 bets;
- 282 StartRound / 282 LockRound / 282 EndRound events;
- 284 replay rounds;
- 2,615 real Chainlink `AnswerUpdated` events;
- source SHA `2cb5dcb374880b20e4b6f859991bea92dce6ba95`;
- source event SHA `a831220e32173a78e879df4024e60f4ffcba6e19`.

The route proof is anchored at block `116844485` using evidence SHA-256 `88991ebf1802fbcdd399f5bc477f19facdf60de3a2b582b1e39f14c1a16ca0e3`, proxy `0x0567f2323251f0aab15c8dfb1967e4e8a7d42aee`, and aggregator `0xa6e8fee84f9bd528ad71917c9ddbb1fd3214f280`. The fixed proof found zero Prediction `NewOracle` and zero proxy `AggregatorConfirmed` events and required no historical state read.

### One-day economic OOS plumbing

`evidence/recent-economic-smoke-2026-08-18-to-19-last-success.json` records:

- 284 candidate rounds;
- 268 research feature rows;
- 14 rounds skipped for unavailable aligned market data;
- 4 purged/embargoed OOS folds;
- 159 direction signals;
- 230 independent pool projections;
- 159 scored OOS outcomes;
- checksum-verified Binance Spot and USD-M provenance with 250 ms availability lags;
- Chainlink availability lag 1000 ms.

The original economic job `96810154525` remains correctly recorded as a GitHub failure because repository evidence persistence failed after analytical computation succeeded. The artifact-bound recovery preserves that distinction rather than relabeling the original job.

Probability diagnostics remain weak: Brier skill score is `-0.12435269318908326` on only 159 scored samples. Positive one-day PnL is therefore not profitability evidence.

### One-day economic sensitivity and feature ablation

`evidence/recent-economic-robustness-2026-08-18-to-19-last-success.json` records `success=true` with a non-vacuous robustness semantic gate:

- 8 exact sensitivity scenarios;
- 8/8 positive-PnL observations;
- baseline PnL `85367433627531589` wei, ROI `55433` ppm;
- worst-case `combined-stress` PnL `19007678140802798` wei, ROI `25343` ppm;
- 5 feature-ablation variants over 159 common OOS epochs.

The full feature set was not uniquely best. Removing `round_history` improved one-day Brier/Brier-skill and PnL relative to `full-v1`, while removing `settlement_source` improved probability-loss metrics but reduced PnL. This result is recorded as a reason to expand across regimes, not as a feature-selection conclusion.

The evidence explicitly retains:

- `profitability_gate_eligible=false`;
- `full_historical_gate_satisfied=false`.

### Stage 5B local-fork readiness

`evidence/stage5b-fork-last-success.json` records observed run `32494992355`:

- loopback Anvil BSC fork only;
- 5 execution intents;
- finalized Bull 2 / Bear 2;
- unresolved intents 0;
- restart recovery true;
- dropped/replaced recovery true;
- reorg reconciliation true;
- non-loopback rejection true;
- verification `ready=true`;
- `signing_enabled=false`;
- `live_broadcast=false`.

This is local-fork execution-readiness evidence, not funded/mainnet execution evidence.

## Current expansion

The Aug 16–19 three-day source path is now authenticated-only. Latest attempt `33184368365` at source/event SHA `5b211ced521091e4c89dd5da93d99491ab28aed9` remained correctly blocked with `AUTHENTICATED_RPC_REQUIRED`; no authenticated RPC candidate was available, so no three-day `last-success` source evidence exists yet.

The downstream work has nevertheless been implemented so the next successful source run can proceed without another development pause:

- `.github/workflows/recent-economic-three-day.yml` consumes the exact successful three-day source artifact, ingests Binance Spot and USD-M aggTrades for Aug 16–18 with official CHECKSUM files, and runs a larger purged/embargoed OOS campaign.
- The three-day OOS structural configuration is min-train 300, test 100, calibration 60, pool-min-train 150, pool-window 400, purge 2, embargo 2.
- The OOS semantic gate requires >=650 research rows, >=3 folds, >=250 direction/joint/scored samples, >=400 pool projections, exact source provenance, and exact availability lags.
- `config/recent-economic-sensitivity-aug16-18.json` freezes the eight-scenario sensitivity set for the same source window.
- `.github/workflows/recent-economic-robustness-three-day.yml` runs those eight sensitivity scenarios and five feature-family ablation variants with non-vacuous sample thresholds.
- `src/pancake_prediction/window_comparison.py` and `scripts/compare_recent_economic_windows.py` compare fail-closed robustness Evidence across the one-day and three-day windows. They report full-v1 deltas for Brier/Brier-skill, log loss, ECE, accuracy, PnL, ROI and drawdown, plus feature-variant Brier/PnL rank changes.
- `.github/workflows/recent-economic-window-comparison.yml` validates exact source lineage and fixed windows, requires five common feature variants, and persists comparison Evidence without changing the profitability/historical/signing boundaries.
- `.github/workflows/recent-public-chainlink-three-day.yml` chains the sequence `bootstrap -> economic-three-day -> economic-robustness-three-day -> economic-window-comparison`. Downstream analysis is skipped unless the upstream fail-closed gate succeeds.

GitHub Actions accepted all three reusable downstream workflow references. CI #1082 passed with 333 tests, including the new cross-window comparison tests. No signing, mainnet broadcast, profitability claim, or funded-validation path was added.

## Remaining risk

- `BSC_ARCHIVE_RPC_URL` is still not configured; complete deployment-era historical reconstruction remains blocked on a source that proves the required historical workload.
- Public endpoint retention, access control, and rate limits can change independently.
- A source window containing `NewOracle` or `AggregatorConfirmed` cannot use the single-route anchor proof and requires historical route reconstruction.
- The current probability skill is not strong enough for an alpha claim.
- One-day stress-positive economics do not establish durable profitability or feature necessity.
- The authenticated three-day source must succeed before any three-day economic or cross-window comparison claim exists.

## Follow-up

1. Configure `BSC_LOG_RPC_URL` or `BSC_ARCHIVE_RPC_URL` with an authenticated/log-capable BSC mainnet endpoint.
2. Rerun the authenticated Aug 16–19 source gate and require a real `last-success` artifact/evidence pair.
3. Allow the chained three-day OOS campaign to execute and validate probability/economic sample thresholds.
4. Allow the chained sensitivity/ablation workflow to execute.
5. Allow the chained cross-window comparison to quantify full-v1 metric changes and feature-ranking stability; require this evidence before changing the canonical feature set.
6. Add pre-window warmup only if the larger observed skip pattern demonstrates a real need.
7. Expand to additional independent recent regimes after the three-day gate is non-vacuous.
8. Keep the complete historical gate fail-closed until an authenticated archive-capable source proves the deployment-era workload.
9. Keep profitability interpretation and any funded/mainnet execution behind their separate evidence and authorization gates.

## Reusable knowledge

- Historical source readiness is a capability matrix, not a provider label.
- Recent timestamp resolution must not introduce accidental deep-history dependencies.
- Prediction oracle proxy identity and Chainlink event-emitter identity are distinct.
- A persisted route anchor is usable only when exact bytes/identity/range are validated and all intervening route-change events are exhaustively scanned.
- Availability timestamps are not necessarily total-order keys; source-native ordering must break ties.
- Analytical success, artifact publication, workflow conclusion, and repository evidence persistence are separate claims.
- Sensitivity success and positive short-window PnL do not imply predictive skill or durable profitability.
- Research authority remains separate from signing/live broadcast.
