# v0.7 Research Readiness and Historical-Source Gate

Status: active / bounded recent path proven; full historical gate blocked
Date: 2026-08-22
Related PR: #1 (`agent/v0.7-alpha-research` -> `main`)
Latest verified normal CI: run `32504281549` (#1010), 320 tests passed, 87% coverage, all quality/integration/security jobs green.

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

A separate Aug 16–19 UTC recent source gate was added in commit `d1c8bf083f3338eef89ece956f58593e39d78945`.

Run `32503882364` is currently collecting the three-day Prediction + Chainlink window. Its identity is persisted in `evidence/recent-public-chainlink-2026-08-16-to-19-running.json`.

The three-day gate requires:

- exact Aug 16–19 timestamps;
- real Prediction and Chainlink events;
- at least 800 replay/Start/Lock/End rounds;
- exact persisted anchor identity and digest;
- zero `NewOracle` / `AggregatorConfirmed` route changes;
- artifact publication before success evidence.

No three-day economic result is claimed until that source gate succeeds.

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

1. Complete and validate the Aug 16–19 source gate.
2. Run a larger three-day source-bound OOS campaign.
3. Repeat sensitivity/ablation and compare calibration and feature stability against the one-day evidence.
4. Add pre-window warmup only if observed skip patterns justify it.
5. Expand across additional recent regimes.
6. Prove an authenticated complete historical source before deployment-era profitability interpretation.

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
