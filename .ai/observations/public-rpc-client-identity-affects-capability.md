# Public RPC capability depends on the client HTTP identity

Status: observation
Date: 2026-08-31
Source case: GitHub Issue #25
Confidence: medium

## Context

The Windows Issue #23 Stage 4 bootstrap initially failed at `eth_blockNumber`
with HTTP 403 on several public BSC RPC endpoints. `JsonRpcClient` supplied only
`Content-Type`, so Python urllib added its default `Python-urllib/3.12`
User-Agent.

## Observation

An RPC endpoint's apparent method capability can depend on the HTTP client
identity as well as the JSON-RPC method and workload. Use an explicit,
non-secret project User-Agent before classifying a 403 as evidence that the
endpoint cannot serve the requested RPC method.

This transport fix is only a prerequisite for capability probing. It does not
prove historical state, sustained block-header access, `eth_getLogs` capacity,
or the complete collector workload.

## Evidence

- On the same Windows host, `bsc-rpc.publicnode.com` returned HTTP 200 for
  `eth_blockNumber` with curl's default User-Agent and with
  `pancakeswap-prediction-ai/0.7`, but HTTP 403 with
  `Python-urllib/3.12`.
- After `JsonRpcClient` supplied the project User-Agent, the real three-day
  bootstrap advanced dRPC from `eth_blockNumber` HTTP 403 to
  `eth_getBlockByNumber` HTTP 429, and Blast from `eth_blockNumber` HTTP 403 to
  `eth_getLogs` HTTP 429.
- The same retry still ended `success=false` and `chainlink_collected=false`;
  none of the configured public candidates satisfied the complete Stage 4
  recent Prediction plus Chainlink workload.
- `tests/test_rpc.py` verifies the explicit request header, and the full Windows
  suite passed with 517 tests and 87% coverage after the change.

## Why it matters

Treating a client-fingerprint rejection as a chain-data limitation can waste
time selecting providers or requesting credentials. Treating a successful
basic request after changing User-Agent as full source readiness is equally
unsafe. Both the HTTP transport and the exact RPC workload must be proven.

## Applicability

Use this when a repository-owned JSON-RPC client sees endpoint-wide HTTP 403
before method-specific probing, especially when curl or another client succeeds
from the same host.

## Exceptions / Limitations

Some providers intentionally deny automated or unauthenticated access. Do not
impersonate a browser, evade provider policy, or repeatedly rotate identifiers.
Use a truthful project User-Agent and honor rate limits. HTTP 200 for one method
does not establish suitability for a campaign.

## Related files

- `src/pancake_prediction/rpc.py`
- `tests/test_rpc.py`
- `scripts/run_recent_public_bootstrap.py`
- `artifacts/recent-bootstrap.json`

## Related cases

- GitHub Issue #25
- GitHub Issue #23
