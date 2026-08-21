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

A larger Aug 16–19 recent source gate is now running as the next OOS expansion.

## Verification state

Normal PR CI run `32504281549` (#1010) completed successfully on the current code + External Intelligence lineage:

- Ruff: success;
- mypy strict: success;
- pytest: 320 passed;
- coverage: 87% on the current implementation lineage;
- Bandit: success;
- pip-audit: success;
- ClickHouse integration: success;
- Gitleaks: success;
- pinned legacy 144k-round audit: success.

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

Commit `d1c8bf083f3338eef89ece956f58593e39d78945` adds an independent Aug 16–19 UTC Prediction + Chainlink source gate.

`evidence/recent-public-chainlink-2026-08-16-to-19-running.json` binds the in-flight run:

- source run ID `32503882364`;
- start `1786838400` (2026-08-16 00:00 UTC);
- end `1787097600` (2026-08-19 00:00 UTC);
- fixed anchor block `116844485`;
- persisted-anchor backward route-change proof;
- no signing or live broadcast.

The three-day gate requires real Prediction + Chainlink data, at least 800 replay/Start/Lock/End rounds, exact route identity, zero route-change events, and successful artifact publication before `last-success` is written.

## Remaining risk

- `BSC_ARCHIVE_RPC_URL` is still not configured; complete deployment-era historical reconstruction remains blocked on a source that proves the required historical workload.
- Public endpoint retention, access control, and rate limits can change independently.
- A source window containing `NewOracle` or `AggregatorConfirmed` cannot use the single-route anchor proof and requires historical route reconstruction.
- The current probability skill is not strong enough for an alpha claim.
- One-day stress-positive economics do not establish durable profitability or feature necessity.
- The running three-day source must succeed before any three-day economic claim exists.

## Follow-up

1. Complete the Aug 16–19 source gate and persist exact `last-success` evidence.
2. Run a larger three-day source-bound economic OOS campaign with stricter train/test/calibration sizes where the available rows support it.
3. Repeat sensitivity and feature ablation on the larger window and compare probability calibration, PnL sensitivity, and feature-family stability against the one-day evidence.
4. Add pre-window warmup only if the larger observed skip pattern justifies it.
5. Expand across additional recent regimes.
6. Keep the complete historical gate fail-closed until an authenticated archive-capable route or equivalent full source is proven.
7. Keep profitability interpretation blocked until larger independent OOS and historical evidence support it.

## Reusable knowledge

- Historical source readiness is a capability matrix, not a provider label.
- Recent timestamp resolution must not introduce accidental deep-history dependencies.
- Prediction oracle proxy identity and Chainlink event-emitter identity are distinct.
- A persisted route anchor is usable only when exact bytes/identity/range are validated and all intervening route-change events are exhaustively scanned.
- Availability timestamps are not necessarily total-order keys; source-native ordering must break ties.
- Analytical success, artifact publication, workflow conclusion, and repository evidence persistence are separate claims.
- Sensitivity success and positive short-window PnL do not imply predictive skill or durable profitability.
- Research authority remains separate from signing/live broadcast.
