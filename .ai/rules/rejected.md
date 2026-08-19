# Rejected / Superseded Rules

## Public BSC RPCs Are Never Archival
Status: rejected
Confidence: high that the generalization is invalid

### Rejected rule

`Public BSC RPC endpoints are never archive-capable.`

### Why rejected

Current repository evidence only establishes that the specific tested unauthenticated candidates were not archive-ready for the required historical workload at the time of probing. Endpoint capabilities, provider tiers, authentication requirements, and historical methods can change. Generalizing the observation to every public endpoint would exceed the evidence.

### Replacement

Use the Observation `../observations/archive-capability-must-be-probed.md`: probe the exact historical capability needed and fail closed when it is unavailable.

### Evidence

- `evidence/public-research-input-probe.json`
- `evidence/archive-rpc-preflight.json`

### Applicability

This rejected rule should be remembered whenever an agent is tempted to infer archival capability or incapability purely from a provider/public label.
