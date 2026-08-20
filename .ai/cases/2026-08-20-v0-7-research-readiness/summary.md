# v0.7 Research Readiness and Historical-Source Gate

Status: active / partially unblocked
Date: 2026-08-20
Related PR: #1 (`agent/v0.7-alpha-research` -> `main`)
Validated source revision for current paced public-RPC implementation: `b4514d85ac7ccd3505994d205404bfbf7e5cb198`
Latest persisted quality evidence for that revision: 304 tests passed, 87% coverage, `ready=true`.

## Problem

The repository needs source-bound canonical Prediction and Chainlink data before a real OOS economic campaign can be treated as evidence. The complete deployment-era historical campaign remains blocked because the authenticated full-history BSC source gate is not configured, but bounded recent collection is no longer blocked in the same way.

## Context

PR #1 implements the v0.7 leakage-safe research/economic-validation foundation. The full pipeline expects canonical BSC history, active Chainlink oracle routing, checksum-verified Binance Spot/Perp slices, availability-aware features, purged/embargoed OOS modeling/calibration, independent pool projection, and explicit transaction economics.

## Current root causes / boundaries

### Full historical campaign

`evidence/archive-rpc-preflight.json` remains `configured=false` / `archive_ready=false` because `BSC_ARCHIVE_RPC_URL` is not configured. Public-source probing has shown that historical state access, historical logs, recent logs, and sustained collector behavior are separate capabilities; no unauthenticated public source has yet proved the complete deployment-era Prediction + Chainlink workload.

### Recent public Prediction collection

A former accidental dependency in timestamp-to-block resolution made recent collection probe arbitrary old headers. Replacing the genesis-to-head search with head-local exponential backoff plus local binary search enabled public recent collection without pretending the node is archival.

The two-hour Aug 19 smoke first proved this route. After adding explicit RPC pacing, six-attempt retry handling, and HTTP 429 `Retry-After` support without weakening canonical block-hash validation, the fixed Aug 18–19 one-day Prediction bootstrap also succeeded on `https://rpc-bsc.48.club`.

Persisted one-day Prediction evidence records approximately 192k scanned blocks, 14,496 canonical Prediction events, 8,673 bets, 282 StartRound / 282 LockRound / 282 EndRound events, and deterministic replay evidence for 284 rounds.

### Recent Chainlink collection

The first recent Chainlink implementation exposed a separate identity problem: Prediction `oracle()` returns a Chainlink proxy, while `AnswerUpdated` is emitted by the underlying aggregator. The corrected route proves both the Prediction oracle proxy and the proxy's `aggregator()` implementation are unchanged across the requested window, then collects `AnswerUpdated` from the proven underlying aggregator.

A two-hour Aug 19 public smoke proved that route with 218 real `AnswerUpdated` events. One-day Prediction + Chainlink validation is maintained as a separate gate; do not infer it from Prediction-only one-day success.

## Current solution / status

- Full historical source gate: still fail-closed and blocked.
- Public two-hour recent Prediction collection: proven.
- Public one-day recent Prediction collection: proven with paced/retry-aware RPC policy.
- Public two-hour recent Prediction + Chainlink collection: proven with real `AnswerUpdated` events from the underlying aggregator.
- Public one-day recent Prediction + Chainlink collection: separate empirical gate; use its own `latest` / `last-success` evidence rather than inferring from shorter runs.
- Research/signing safety boundary: unchanged.
- Current software quality gate for the paced RPC source revision: green.

Recent public-source success is a bounded source/readiness result. It does not replace the complete historical gate and is not profitability evidence.

## Related files

- `evidence/archive-rpc-preflight.json`
- `evidence/public-archive-candidate-probe.json`
- `evidence/public-blast-bootstrap-smoke.json`
- `evidence/recent-public-bootstrap-smoke-2026-08-19-last-success.json`
- `evidence/recent-public-bootstrap-2026-08-18-to-19.json`
- `evidence/recent-public-chainlink-smoke-2026-08-19-last-success.json`
- `evidence/recent-public-chainlink-2026-08-18-to-19-latest.json`
- `evidence/recent-public-chainlink-2026-08-18-to-19-last-success.json`
- `evidence/quality-gate.json`
- `src/pancake_prediction/rpc.py`
- `src/pancake_prediction/recent_bootstrap.py`
- `src/pancake_prediction/public_collector.py`
- `scripts/run_recent_public_bootstrap.py`
- `.github/workflows/recent-public-bootstrap.yml`
- `.github/workflows/recent-public-chainlink-day.yml`
- `.github/workflows/archive-rpc-preflight.yml`
- `.github/workflows/historical-bootstrap.yml`

## Related tests / commits

- `b4514d85ac7ccd3505994d205404bfbf7e5cb198`: paced public-RPC implementation validated by normal CI.
- GitHub Actions run 867: complete success for that source revision.
- `evidence/quality-gate.json`: 304 passed tests, 87% coverage, `ready=true` for that source SHA.
- `2d9edc2fc0fc8d571613c385042724008b5e2855`: persisted successful Aug 18–19 public Prediction bootstrap evidence.
- `6ad31bb4629468d45e98171b99d013970fa31c7d`: localize recent timestamp header search.
- `8b42f3c002c43b9c80fa4f65fd1718dd603b3664`: regression coverage for bounded recent header search.
- `394f67c6ca2f3fec33049759bdb37e3590401ae3`: enforce archive RPC preflight gate after evidence persistence.
- `5af84bbca184e6f785282bde1580591afa8a55d4`: fail historical bootstrap when archive credential is absent.
- `61df8e8731b92ea7f30e6011fde02cdd6d0bdd28`: prevent cancelled quality runs from overwriting current evidence and bind checks to trigger SHA.
