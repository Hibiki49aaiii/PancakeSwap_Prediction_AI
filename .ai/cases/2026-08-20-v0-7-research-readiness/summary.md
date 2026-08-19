# v0.7 Research Readiness and Archive Gate

Status: active / blocked
Date: 2026-08-20
Related PR: #1 (`agent/v0.7-alpha-research` -> `main`)
Observed branch head before this knowledge commit: `c15237092edf52e9982a07b93f2ee154d39d3194`

## Problem

The repository has implemented the v0.7 leakage-safe research/economic-validation foundation, but the first real source-bound historical/OOS economic campaign cannot proceed until an archive-capable BSC source is available through the explicit repository gate.

## Context

PR #1 is a draft research PR. The pipeline expects canonical BSC history, active historical Chainlink oracle state, checksum-verified Binance Spot/Perp slices, availability-aware features, purged/embargoed OOS modeling/calibration, independent pool projection, and explicit transaction economics.

## Root cause

The current persisted archive preflight says the `BSC_ARCHIVE_RPC_URL` gate is not configured, so `configured=false` and `archive_ready=false`. Public-candidate probing did not find an unauthenticated archive-ready endpoint for the exact required historical-state workload.

The current persisted quality evidence also reports `ruff=failure` and `ready=false` while mypy, pytest, Bandit, and pip-audit are successful. The cause of the Ruff failure is not recorded here because the available persisted evidence does not establish it; do not infer one.

## Final solution / status

The archive blocker is intentionally not bypassed. A secret-backed preflight and historical-bootstrap workflow already exist; the next real campaign remains blocked until an authenticated archive-capable endpoint is configured and passes the gate. The External Intelligence addition does not alter source behavior, research assumptions, signing boundaries, or the campaign gate.

## Related files

- `evidence/archive-rpc-preflight.json`
- `evidence/public-research-input-probe.json`
- `evidence/binance-real-sample-2026-08-01.json`
- `evidence/quality-gate.json`
- `.github/workflows/archive-rpc-preflight.yml`
- `.github/workflows/historical-bootstrap.yml`
- `docs/CAMPAIGN_EVALUATION.md`
- `docs/STAGE5_FORK_EXECUTION.md`

## Related tests / commits

- current test suite under `tests/`
- `7579615b0ea4c9a941e37188efa6f77a3444e38e`: dedicated Anvil reorg injection coverage
- `c15237092edf52e9982a07b93f2ee154d39d3194`: persisted quality evidence update
