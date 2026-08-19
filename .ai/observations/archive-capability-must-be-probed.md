# Archive Capability Must Be Probed
Status: observation
Date: 2026-08-20
Source case: `../cases/2026-08-20-v0-7-research-readiness/`
Confidence: medium

## Context

The v0.7 historical bootstrap requires historical BSC state around the PancakeSwap Prediction deployment era, not merely a reachable BSC JSON-RPC endpoint.

## Observation

Archive capability is an empirical property of the endpoint and the exact historical requests needed by the workload. A provider name, public/private label, successful `eth_chainId`, or current-head access is not sufficient evidence.

## Evidence

- `evidence/public-research-input-probe.json` currently reports `archive_ready_endpoints: []` for the tested public candidates and records failures including missing trie state, unsupported historical state, and authentication-related failures.
- `evidence/archive-rpc-preflight.json` currently reports `configured=false` and `archive_ready=false` for the explicit authenticated archive gate.
- `.github/workflows/archive-rpc-preflight.yml` performs the secret-backed capability check and persists only redacted readiness evidence.

## Why it matters

Treating reachability as archive readiness can waste a historical collection run or, worse, produce a partial-data path that looks operational while lacking the exact historical state required for canonical reconstruction.

## Applicability

Use this when selecting or changing a BSC RPC for historical state, old-contract bytecode, or historical `eth_call` workloads.

## Exceptions / Limitations

This observation does not prove that all public endpoints are non-archival. Endpoint capabilities can change, authenticated tiers differ, and a different historical workload may require different methods. Re-probe the actual requests.

## Related files

- `scripts/probe_public_research_inputs.py`
- `scripts/probe_archive_rpc_secret.py`
- `.github/workflows/archive-rpc-preflight.yml`
- `evidence/public-research-input-probe.json`
- `evidence/archive-rpc-preflight.json`

## Related cases

- `../cases/2026-08-20-v0-7-research-readiness/`
