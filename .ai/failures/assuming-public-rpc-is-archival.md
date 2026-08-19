# Assuming a Public RPC Is Archival

## Context

Historical BNBUSD reconstruction needs old PancakeSwap Prediction contract state/bytecode and historical calls around the deployment era.

## Attempt

Treat candidate public BSC endpoints as plausible historical sources based on normal JSON-RPC reachability/provider availability before proving the exact archive-state workload.

## Why it seemed plausible

Several candidates return the correct chain ID/current head and can answer some transaction/receipt or current-state requests, so a superficial health check can look sufficient.

## Why it failed

The persisted probe found no tested unauthenticated candidate that was archive-ready for the required workload. Recorded failure modes include missing trie state, unsupported historical state, request limits, and authentication/provider-tier requirements.

## Evidence

- `evidence/public-research-input-probe.json`: `archive_ready_endpoints` is currently empty and per-endpoint errors capture the failure modes.
- `evidence/archive-rpc-preflight.json`: the explicit secret-backed gate is currently not configured/archive-ready.
- `.github/workflows/archive-rpc-preflight.yml`: capability is tested before historical bootstrap proceeds.

## Better approach

Probe the exact old bytecode/historical `eth_call`/state behavior needed by the collector, then fail closed. Use the authenticated `BSC_ARCHIVE_RPC_URL` gate when configured. Persist only redacted readiness evidence, never the secret value.

## Applicability

Historical BSC collection, fork source selection, or any task that depends on old EVM state. Re-run the probe when endpoints/provider tiers change; do not convert this failure memory into the false rule that all public endpoints are permanently non-archival.
