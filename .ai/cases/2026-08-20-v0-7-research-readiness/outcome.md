# Outcome

## Current outcome

The v0.7 research foundation remains intact and the archive gate remains fail-closed. External Intelligence was added as documentation/control metadata only; no production/research Python, SQL, workflow behavior, execution permission, or economic assumption was changed by this case.

## Verification state

Before the knowledge commit, persisted quality evidence showed pytest/mypy/Bandit/pip-audit success but Ruff failure and `ready=false`. The External Intelligence change is Markdown-only, so it should not alter Python behavior, but the PR CI result after the commit is the authoritative regression check.

## Remaining risk

- authenticated archive access is still absent until `BSC_ARCHIVE_RPC_URL` is configured and passes preflight;
- the exact current Ruff diagnostic must be inspected/fixed separately rather than guessed from the summary evidence;
- historical integrity, OOS economics, shadow behavior, and local-fork execution gates remain distinct and must not be collapsed into a profitability/readiness claim.

## Follow-up

After the archive gate passes:

1. run the prepared BNBUSD historical bootstrap;
2. bind the exact canonical history/oracle timeline and Binance source window;
3. ingest checksum-verified Spot/Perp data under realistic positive availability-lag scenarios;
4. run source-bound `campaign-evaluate` with explicit stake/gas/inclusion latency;
5. compare sensitivity/ablation/regime stability;
6. continue to shadow/local-fork gates only when their own evidence criteria are met.

## Reusable knowledge

- Archive capability must be empirically probed for the exact historical workload.
- Successful software/data ingestion is not profitability evidence.
- Keep research authority separate from transaction signing/live broadcast.
