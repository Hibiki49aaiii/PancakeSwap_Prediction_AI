# Historical RPC Capabilities Must Be Probed Independently
Status: observation
Date: 2026-08-20
Source case: `../cases/2026-08-20-v0-7-research-readiness/`
Confidence: high

## Context

The v0.7 historical bootstrap needs several distinct BSC capabilities around the PancakeSwap Prediction deployment era: historical block headers, historical contract state (`eth_getCode` / `eth_call`), and sustained historical event collection (`eth_getLogs` or an evidence-equivalent source). A reachable BSC endpoint does not imply that all of these capabilities are available under the same quota or provider policy.

## Observation

Historical-source readiness is a capability matrix, not a single `archive` boolean. Probe the exact methods and workload shape independently, then prove a bounded end-to-end collector run before promoting an endpoint to a historical data source.

In particular, historical state access does not establish historical log-collection readiness. Provider name, public/private label, successful `eth_chainId`, current-head access, or a successful historical `eth_call` is insufficient by itself.

## Evidence

- `evidence/public-research-input-probe.json` records multiple public candidates that can answer current/creation queries but fail historical state and/or historical logs through missing trie state, unsupported methods, range limits, or authentication requirements.
- `evidence/public-archive-candidate-probe.json` records one unauthenticated candidate, `https://bsc-mainnet.public.blastapi.io`, that returned `archive_ready=true` for the exact historical `eth_getCode` + `eth_call` probe while its historical `eth_getLogs` request was rate-limited. This corrects the older case state that no tested public endpoint had archive-state capability.
- `evidence/public-blast-bootstrap-smoke.json` then used the real repository RPC client with six attempts and failed before collection on a one-block deployment-era `eth_getLogs` request with HTTP 403. `success=false`; no bootstrap or oracle-history result was produced.
- `evidence/archive-rpc-preflight.json` still records `configured=false` and `archive_ready=false` for the explicit authenticated repository gate.
- `.github/workflows/public-blast-bootstrap-smoke.yml` deliberately requires a bounded real collector + SQLite + quality/replay + active-oracle-history path rather than accepting the state probe alone.

## Why it matters

Treating an endpoint as usable because one archive-state request succeeds can produce a source that passes preflight but cannot collect the canonical event history required for research. Conversely, a failure in one capability does not justify the claim that every other historical capability is absent.

## Applicability

Use this when selecting or changing a BSC source for:

- historical contract state;
- deployment-era bytecode or `eth_call`;
- Prediction/Chainlink event collection;
- historical bootstrap or local-fork source selection;
- any future source fallback intended to replace the authenticated archive gate.

## Exceptions / Limitations

Capability and rate-limit behavior can change over time and by authentication/provider tier. The Blast evidence proves only the observed requests at the recorded time: archive-state access succeeded, while collector-compatible historical logs did not. It is not evidence that Blast can never support the workload, nor that an authenticated tier would behave the same way.

## Related files

- `scripts/probe_public_research_inputs.py`
- `scripts/probe_public_block_receipts.py`
- `scripts/probe_archive_rpc_secret.py`
- `.github/workflows/public-archive-candidate-probe.yml`
- `.github/workflows/public-blast-bootstrap-smoke.yml`
- `.github/workflows/archive-rpc-preflight.yml`
- `evidence/public-research-input-probe.json`
- `evidence/public-archive-candidate-probe.json`
- `evidence/public-blast-bootstrap-smoke.json`
- `evidence/archive-rpc-preflight.json`

## Related cases

- `../cases/2026-08-20-v0-7-research-readiness/`
