# Assuming a Public RPC Is a Complete Historical Source

## Context

Historical BNBUSD reconstruction needs old PancakeSwap Prediction state plus sustained canonical Prediction/Chainlink event collection. These are separate RPC capabilities and can be subject to different method policies and rate limits.

## Attempt

Promote a public BSC endpoint to a historical source based on reachability, provider reputation, or success of only one historical method such as `eth_getCode` / `eth_call`.

## Why it seemed plausible

Several candidates return the correct chain ID/current head and old creation transaction/receipt data. A later Blast public probe went further and successfully returned the exact historical `eth_getCode` plus `eth_call` used by the archive-state gate, so a state-only check could reasonably appear sufficient.

## Why it failed

Capability did not compose automatically across methods. The original public probe recorded missing trie state, unsupported historical state, request/range limits, and authentication requirements on several endpoints. The later Blast probe returned `archive_ready=true` for historical state but failed historical `eth_getLogs`; a bounded real collector smoke then failed its one-block deployment-era `eth_getLogs` after six attempts with HTTP 403. No SQLite bootstrap or active-oracle history was produced.

## Evidence

- `evidence/public-research-input-probe.json`: original public candidates expose mixed creation/current/history capabilities and no endpoint satisfied the full original readiness shape.
- `evidence/public-archive-candidate-probe.json`: Blast passed historical state while historical logs were rate-limited; other added candidates hit public-tier limits or availability failures.
- `evidence/public-blast-bootstrap-smoke.json`: `success=false`, one-block historical log request failed after six attempts with HTTP 403.
- `evidence/archive-rpc-preflight.json`: the explicit authenticated gate remains not configured/archive-ready.
- `.github/workflows/public-blast-bootstrap-smoke.yml`: requires a bounded real bootstrap rather than accepting a state-only probe.

## Better approach

Probe a capability matrix for the exact workload: historical headers/state, historical logs, provider limits, and then a bounded end-to-end collector run with the repository's real retry/chunking behavior. Promote a source only after that path succeeds. Keep the authenticated `BSC_ARCHIVE_RPC_URL` gate fail-closed until an alternative has equivalent evidence. Persist only redacted readiness evidence, never a credential value.

## Applicability

Historical BSC collection, fork source selection, or any task depending on old EVM state/events. Re-run probes when endpoint/provider tiers change. This memory must not be converted into the false rule that all public endpoints are permanently non-archival: Blast directly demonstrated that a public endpoint can expose historical state while still being unsuitable for the complete collector workload.
