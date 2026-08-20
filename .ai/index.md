# External Intelligence Index

Read this file first, then open only entries relevant to the current task.

| Title | Type | Short description | Keywords | Status | Path |
|---|---|---|---|---|---|
| v0.7 research readiness and archive gate | Case | Current OOS campaign readiness, historical-source blocker, public-source probes, and evidence boundaries | v0.7, BNBUSD, archive, RPC, OOS, CI, blast | active / blocked | `cases/2026-08-20-v0-7-research-readiness/summary.md` |
| Historical RPC capabilities must be probed independently | Observation | State, logs, and sustained collector capability are distinct; prove the exact workload before source promotion | bsc, rpc, archive, eth_call, eth_getLogs, rate-limit, collector | observation / high | `observations/archive-capability-must-be-probed.md` |
| Evidence-preserving CI gates must re-enforce failure | Candidate Rule | `continue-on-error` may preserve evidence but must not silently turn a failed readiness gate green | github-actions, ci, gate, continue-on-error, fail-closed, evidence | candidate / medium | `rules/candidates.md` |
| Research and execution safety boundary | Decision | Signing/mainnet authority is excluded; transaction-capable work remains loopback local-fork | signing, private-key, fork, loopback, execution, security | active | `decisions/research-execution-safety-boundary.md` |
| Profitability evidence boundary | Validated Rule | CI, parser, ingestion, or one backtest cannot establish trading profitability | profitability, OOS, costs, latency, backtest, CI | validated / high | `rules/validated.md` |
| Assuming public RPC is archival | Failure | Avoid selecting an endpoint by label/provider or a single method probe; require the exact historical workload | bsc, rpc, archive, public, historical, logs, state | active | `failures/assuming-public-rpc-is-archival.md` |
| Public BSC RPCs are never archival | Rejected Rule | Overgeneralization rejected; capability is endpoint/time/request specific | bsc, rpc, archive, generalization | rejected | `rules/rejected.md` |
| Repository decision invariants | Intelligence | Compact research, leakage, economics, provenance, and safety invariants | architecture, leakage, provenance, economics, safety | active | `intelligence/repository-invariants.md` |
