# Evidence

## Archive gate

`evidence/archive-rpc-preflight.json` currently records:

- `configured: false`
- `archive_ready: false`
- reason: the required repository secret is not configured

`.github/workflows/archive-rpc-preflight.yml` reads the secret into the workflow environment, runs `scripts/probe_archive_rpc_secret.py`, commits only the redacted JSON result, and uploads the redacted artifact.

## Public-input probe

`evidence/public-research-input-probe.json` currently records `archive_ready_endpoints: []` for the tested candidates. Individual failures include missing trie state, unsupported historical state, request limits, and authentication requirements. This evidence is time- and endpoint-specific; it does not justify a universal claim about all public RPCs.

## Real Binance archive validation

`evidence/binance-real-sample-2026-08-01.json` records:

- checksum verification for BNBUSDT Spot and USD-M Futures sources;
- 147,014 Spot rows and 169,935 UM-Futures rows after ingest;
- ClickHouse schema readiness and successful ingest/inspect steps;
- `validation_ready: true` for this parser/ingest validation;
- an explicit note that zero availability lag in this workflow is not profitability evidence.

## Quality evidence history

Before the lint repair, persisted quality evidence and GitHub Actions runs 788/789 established a narrow failure shape:

- mypy: success
- pytest: success, 294 passed
- Bandit: success
- pip-audit: success
- Gitleaks: success
- ClickHouse integration: success
- legacy 144k-round audit: success
- Ruff: failure

The job log established the exact Ruff diagnostics:

- `src/pancake_prediction/execution_intent.py:9:1`: `UP035` — import `Mapping` from `collections.abc`
- `src/pancake_prediction/stage5_evidence.py:11:1`: `UP035` — import `Mapping` from `collections.abc`

The source fixes were deliberately minimal:

- `27c6d95f9513a49f80ccb5e5d241aeb3f5c36e20` moved `Mapping` in `stage5_evidence.py`;
- `ae25cf0a4915f953fda7d1dac4042133b1d76f0e` moved `Mapping` in `execution_intent.py`.

The commit diffs contain no execution-logic changes; the latter also normalizes the file's final newline.

GitHub Actions CI run 791 on `ae25cf0a4915f953fda7d1dac4042133b1d76f0e` completed successfully:

- Ruff: success
- mypy strict: success
- pytest: success, 294 passed
- Bandit: success
- pip-audit: success
- Gitleaks: success
- ClickHouse integration: success
- legacy 144k-round audit: success
- final quality-gate enforcement: success

The repository automation subsequently committed `f38910027e381605b12952f0bd8ad718ed534bd7`, updating `evidence/quality-gate.json` to:

- `ready: true`
- `ruff: success`
- `source_sha: ae25cf0a4915f953fda7d1dac4042133b1d76f0e`

This lint incident remains case-level evidence rather than a generalized Observation/Failure/Rule because the repair is mechanically recoverable from current source and Ruff output and does not materially change future architectural decisions.

## Repository-level invariants

`README.md` and PR #1 establish that accuracy/green infrastructure checks are insufficient for profitability claims and that the current transaction-capable path remains loopback/local-fork only with no signing authority in the research layer.
