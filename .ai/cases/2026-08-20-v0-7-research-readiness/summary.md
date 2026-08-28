# v0.7 Research Readiness and Historical-Source Gate

Status: active / bounded one-day path proven; three-day source blocked on authenticated RPC; downstream three-day OOS/robustness pipeline prepared
Date: 2026-08-29
Related PR: #1 (`agent/v0.7-alpha-research` -> `main`)
Latest verified normal CI: run `33183757586` (#1076), 330 tests passed and all CI jobs green.

## Problem

The repository requires source-bound canonical Prediction, active Chainlink, checksum-verified Binance market data, and leakage-safe OOS evaluation before economic output can be treated as research evidence. The complete deployment-era campaign is still blocked because the authenticated full-history BSC source gate is not configured.

Bounded recent validation is no longer blocked: one-day source, one-day economic plumbing, one-day sensitivity/ablation, and local-fork Stage 5B readiness have all produced persisted empirical evidence.

## Current boundaries

### Full historical campaign

`BSC_ARCHIVE_RPC_URL` is still not configured. Public-source probing showed that historical state, historical logs, recent logs, and sustained collection are independent capabilities. No unauthenticated public source has proved the complete deployment-era Prediction + Chainlink workload.

This gate remains fail-closed.

### Recent Prediction + Chainlink route

Recent timestamp-to-block resolution now stays local to the requested window instead of probing arbitrary old headers.

Prediction `oracle()` is a Chainlink proxy while `AnswerUpdated` is emitted by the underlying aggregator. Historical block-tagged state reads for the Aug 18 window were unavailable on the tested public routes, so the working recent proof uses a persisted successful Aug 19 route anchor plus fixed stateless backward scans for `NewOracle` and `AggregatorConfirmed`.

The fixed anchor is:

- block `116844485`;
- proxy `0x0567f2323251f0aab15c8dfb1967e4e8a7d42aee`;
- aggregator `0xa6e8fee84f9bd528ad71917c9ddbb1fd3214f280`;
- anchor evidence SHA-256 `88991ebf1802fbcdd399f5bc477f19facdf60de3a2b582b1e39f14c1a16ca0e3`.

One-day source run `32481332419` proved:

- blocks `116556542..116748497`;
- 14,496 canonical Prediction events;
- 8,673 bets;
- 282 Start / 282 Lock / 282 End events;
- 284 replay rounds;
- 2,615 real Chainlink `AnswerUpdated` events;
- zero route-change events in the fixed proof range.

### One-day economic OOS plumbing

The source-bound Aug 18 economic campaign proved non-vacuous plumbing:

- 268 research rows from 284 candidate rounds;
- 4 purged/embargoed OOS folds;
- 159 scored OOS samples;
- 230 independent pool projections;
- exact Spot/Perp provenance with 250 ms lags;
- Chainlink availability lag 1000 ms.

The original economic GitHub job failed only after successful analytical computation because its repository-persistence shell used a broken nested heredoc. Recovered evidence is artifact/SHA bound and preserves the original job failure provenance. The canonical persist step has been repaired.

Probability evidence remains weak: the baseline Brier skill score is `-0.12435269318908326`, so positive one-day PnL is not treated as alpha evidence.

### One-day sensitivity and ablation

`evidence/recent-economic-robustness-2026-08-18-to-19-last-success.json` records a successful robustness gate:

- 8/8 exact economic scenarios evaluated non-vacuously;
- all 8 happened to produce positive one-day PnL;
- worst case `combined-stress`: `19007678140802798` wei PnL, `25343` ppm ROI;
- 5 feature-family ablation variants over 159 common OOS epochs.

The full feature set was not uniquely best. Removing `round_history` improved both one-day probability-loss metrics and realized PnL; removing `settlement_source` improved Brier/Brier-skill while reducing PnL. This blocks any claim that the current feature set is proven optimal.

### Stage 5B

Observed run `32494992355` proved the local-fork execution-readiness gate:

- loopback Anvil BSC fork only;
- restart, dropped/replaced, reorg, and non-loopback rejection scenarios passed;
- unresolved intents 0;
- `signing_enabled=false`;
- `live_broadcast=false`.

No funded/mainnet execution path is introduced.

## Current expansion

The earlier public-only Aug 16–19 attempt is no longer treated as a useful retry path. The three-day source workflow is now authenticated-only and remains fail-closed until one of the repository secrets `BSC_LOG_RPC_URL` or `BSC_ARCHIVE_RPC_URL` is configured.

Latest observed three-day source attempt:

- workflow run `33183751676`;
- source SHA/event SHA `6b322ccecb4ad04091fc530f62cd1a063c1712ce`;
- `source_requirement.classification="AUTHENTICATED_RPC_REQUIRED"`;
- no authenticated candidate was selected;
- no three-day `last-success` evidence was created;
- signing and live broadcast remained disabled.

Development continued past that external blocker by preparing the downstream analysis path:

1. `.github/workflows/recent-economic-three-day.yml` binds to the exact successful three-day source run/artifact and ingests checksum-verified Binance Spot + USD-M aggTrades for Aug 16, 17, and 18.
2. The three-day OOS campaign uses stricter structural sizes than the one-day smoke: min train 300, test 100, calibration 60, pool min train 150, pool window 400, purge 2, embargo 2.
3. Its semantic gate requires at least 650 research rows, at least 3 folds, at least 250 scored/joint/direction samples, at least 400 pool projections, and all source-lag/provenance checks.
4. `.github/workflows/recent-economic-robustness-three-day.yml` then runs the same eight economic sensitivity scenarios plus five feature-family ablation variants on the exact three-day source.
5. `.github/workflows/recent-public-chainlink-three-day.yml` now chains `bootstrap -> economic-three-day -> economic-robustness-three-day`; each downstream job is skipped unless the prior fail-closed gate succeeds.

The new workflow definitions are accepted by GitHub Actions, and normal CI #1076 passed with 330 tests. No profitability or funded-execution claim was introduced.

## Interpretation boundary

Current evidence establishes software correctness, bounded recent data acquisition, economic-pipeline robustness, and local-fork execution-readiness as separate claims.

It does **not** establish durable profitability because:

- the sample is short;
- probability skill is negative on the one-day window;
- the full feature set is not stable as the best ablation variant;
- broader recent regimes have not yet been evaluated;
- the full historical-source gate remains unsatisfied.

All current economic evidence keeps `profitability_gate_eligible=false` and `full_historical_gate_satisfied=false`.

## Next steps

1. Configure one authenticated/log-capable BSC mainnet RPC secret: `BSC_LOG_RPC_URL` or `BSC_ARCHIVE_RPC_URL`.
2. Rerun `recent-authenticated-chainlink-three-day` and require exact source/route/event/artifact semantics to produce `recent-public-chainlink-2026-08-16-to-19-last-success.json`.
3. Let the chained three-day OOS workflow execute and inspect probability skill, calibration, PnL/drawdown, source lineage, and the explicit minimum sample gates.
4. Let the chained three-day robustness workflow run the eight sensitivity scenarios and five feature-family ablations; compare stability against the one-day evidence instead of choosing features from the one-day result alone.
5. Expand across additional independent recent market regimes only after the three-day evidence is non-vacuous.
6. Separately prove the complete deployment-era historical source with an archive-capable route before any full-history profitability interpretation.
7. Keep research authority separate from signing/live broadcast and keep Stage 6B funded validation behind a separate explicit operational/legal gate.

## Related evidence

- `evidence/recent-public-chainlink-2026-08-18-to-19-last-success.json`
- `evidence/recent-economic-smoke-2026-08-18-to-19-last-success.json`
- `evidence/recent-economic-robustness-2026-08-18-to-19-last-success.json`
- `evidence/stage5b-fork-last-success.json`
- `evidence/recent-public-chainlink-2026-08-16-to-19-running.json`
- `evidence/quality-gate.json`

## Related observations

- `../../observations/archive-capability-must-be-probed.md`
- `../../observations/recent-bootstrap-must-search-headers-locally.md`
- `../../observations/chainlink-proxy-vs-aggregator.md`
- `../../observations/source-native-order-breaks-timestamp-ties.md`
- `../../observations/evidence-persist-failure-is-not-analytical-failure.md`
- `../../observations/one-day-economic-robustness-is-not-alpha-proof.md`
