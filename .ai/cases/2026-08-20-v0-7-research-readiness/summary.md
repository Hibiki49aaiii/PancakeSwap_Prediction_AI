# v0.7 Research Readiness and Archive Gate

Status: active / blocked
Date: 2026-08-20
Related PR: #1 (`agent/v0.7-alpha-research` -> `main`)
Observed branch head after CI recovery: `f38910027e381605b12952f0bd8ad718ed534bd7`

## Problem

The repository has implemented the v0.7 leakage-safe research/economic-validation foundation, but the first real source-bound historical/OOS economic campaign cannot proceed until an archive-capable BSC source is available through the explicit repository gate.

## Context

PR #1 is a draft research PR. The pipeline expects canonical BSC history, active historical Chainlink oracle state, checksum-verified Binance Spot/Perp slices, availability-aware features, purged/embargoed OOS modeling/calibration, independent pool projection, and explicit transaction economics.

## Root cause

The remaining campaign blocker is the archive-input gate. `evidence/archive-rpc-preflight.json` currently records `configured=false` and `archive_ready=false` because `BSC_ARCHIVE_RPC_URL` is not configured. Public-candidate probing did not find an unauthenticated archive-ready endpoint for the exact required historical-state workload.

A separate repository quality failure observed during this case was traced to Ruff `UP035` diagnostics in two Stage 5 files. Both `Mapping` imports were moved from `typing` to `collections.abc` without changing execution logic. GitHub Actions run 791 then passed the full CI quality gate, and the repository's persisted quality evidence was updated to `ready=true` with source SHA `ae25cf0a4915f953fda7d1dac4042133b1d76f0e`.

## Final solution / status

The archive blocker remains intentionally fail-closed and was not bypassed. The unrelated Ruff regression is resolved. External Intelligence remains a selective documentation/control layer and does not change research assumptions, signing boundaries, transaction authority, or campaign gates.

## Related files

- `evidence/archive-rpc-preflight.json`
- `evidence/public-research-input-probe.json`
- `evidence/binance-real-sample-2026-08-01.json`
- `evidence/quality-gate.json`
- `.github/workflows/archive-rpc-preflight.yml`
- `.github/workflows/historical-bootstrap.yml`
- `src/pancake_prediction/execution_intent.py`
- `src/pancake_prediction/stage5_evidence.py`
- `docs/CAMPAIGN_EVALUATION.md`
- `docs/STAGE5_FORK_EXECUTION.md`

## Related tests / commits

- GitHub Actions CI run 791: success
- pytest: 294 passed in the successful run
- `27c6d95f9513a49f80ccb5e5d241aeb3f5c36e20`: fix Stage 5 evidence `Mapping` import
- `ae25cf0a4915f953fda7d1dac4042133b1d76f0e`: fix execution-intent `Mapping` import
- `f38910027e381605b12952f0bd8ad718ed534bd7`: persist `ready=true` source quality evidence
- `7579615b0ea4c9a941e37188efa6f77a3444e38e`: dedicated Anvil reorg injection coverage
