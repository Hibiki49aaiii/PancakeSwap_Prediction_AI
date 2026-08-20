# v0.7 Research Readiness and Historical-Source Gate

Status: active / partially unblocked
Date: 2026-08-20
Related PR: #1 (`agent/v0.7-alpha-research` -> `main`)
Validated source revision: `594ed3c44ae5fb251b54456f4fae59c7ae0b4032`
Latest quality-evidence commit before this knowledge update: `d9957261f5b28926e534f806e2ff70c13805a1eb`

## Problem

The repository needs source-bound canonical Prediction and Chainlink data before the first real OOS economic campaign. The complete historical campaign is still blocked because the authenticated full-history BSC source gate is not configured, but recent data collection no longer needs to be treated as equally blocked.

## Context

PR #1 implements the v0.7 leakage-safe research/economic-validation foundation. The full pipeline expects canonical BSC history, active Chainlink oracle routing, checksum-verified Binance Spot/Perp slices, availability-aware features, purged/embargoed OOS modeling/calibration, independent pool projection, and explicit transaction economics.

## Current root causes / boundaries

### Full historical campaign

`evidence/archive-rpc-preflight.json` remains `configured=false` / `archive_ready=false` because `BSC_ARCHIVE_RPC_URL` is not configured. Public-source probing has shown that historical state access, historical logs, recent logs, and sustained collector behavior are separate capabilities; no public source has yet proved the complete deployment-era Prediction + Chainlink workload.

### Recent public collection

An accidental dependency in timestamp-to-block resolution made recent collection probe arbitrary old headers. Replacing the genesis-to-head search with head-local exponential backoff plus local binary search allowed a public full-node source with recent-log capability to complete a two-hour canonical Prediction smoke.

The first recent Chainlink implementation then exposed a second issue: the Prediction `oracle()` address is a Chainlink proxy, while `AnswerUpdated` is emitted by the underlying aggregator. The current route proves both the Prediction proxy and the proxy's `aggregator()` implementation are unchanged across the recent window, then collects `AnswerUpdated` from that proven underlying aggregator.

## Current solution / status

- Full historical source gate: still fail-closed and blocked.
- Public two-hour recent Prediction collection: proven.
- Public two-hour recent Prediction + Chainlink collection: proven with real `AnswerUpdated` events.
- One-day public Prediction collection: not yet reliable because public RPC rate limits still interrupt the larger run.
- Research/signing safety boundary: unchanged.
- Current software quality gate: green.

The recent public route is a separate evidence tier. It does not replace the full historical gate and is not profitability evidence.

## Related files

- `evidence/archive-rpc-preflight.json`
- `evidence/public-archive-candidate-probe.json`
- `evidence/public-blast-bootstrap-smoke.json`
- `evidence/recent-public-bootstrap-smoke-2026-08-19.json`
- `evidence/recent-public-chainlink-smoke-2026-08-19.json`
- `evidence/recent-public-bootstrap-2026-08-18-to-19.json`
- `evidence/quality-gate.json`
- `src/pancake_prediction/recent_bootstrap.py`
- `src/pancake_prediction/public_collector.py`
- `scripts/run_recent_public_bootstrap.py`
- `.github/workflows/recent-public-chainlink-smoke.yml`
- `.github/workflows/archive-rpc-preflight.yml`
- `.github/workflows/historical-bootstrap.yml`

## Related tests / commits

- GitHub Actions CI run 856: complete success on `594ed3c44ae5fb251b54456f4fae59c7ae0b4032`.
- `evidence/quality-gate.json`: 300 passed, 87% coverage, `ready=true` for that source SHA.
- `6ad31bb4629468d45e98171b99d013970fa31c7d`: localize recent timestamp header search.
- `8b42f3c002c43b9c80fa4f65fd1718dd603b3664`: regression coverage for bounded recent header search.
- Recent public Chainlink commits add stateless proxy/aggregator stability proof and underlying-aggregator event collection; current behavior is covered by `tests/test_public_collector.py` and the real smoke evidence.
- `394f67c6ca2f3fec33049759bdb37e3590401ae3`: enforce archive RPC preflight gate after evidence persistence.
- `5af84bbca184e6f785282bde1580591afa8a55d4`: fail historical bootstrap when archive credential is absent.
- `61df8e8731b92ea7f30e6011fde02cdd6d0bdd28`: prevent cancelled quality runs from overwriting current evidence and bind checks to trigger SHA.
