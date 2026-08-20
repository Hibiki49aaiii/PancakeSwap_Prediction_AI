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

## Quality evidence

`evidence/quality-gate.json` at the observed pre-External-Intelligence head records:

- mypy: success
- pytest: success, 294 passed
- Bandit: success
- pip-audit: success
- Ruff: failure
- ready: false

GitHub Actions CI run `788` on the External Intelligence head independently reproduced the same quality shape:

- Gitleaks: success
- legacy 144k-round audit: success
- ClickHouse integration: success
- installed CLI smoke checks: success
- mypy strict: success (`140 source files` reported by the job)
- pytest: success (`294 passed`, total coverage `87%`)
- Bandit: success (`0 issues identified`)
- pip-audit: success (no known dependency vulnerabilities reported)
- Ruff: failure

The CI job log establishes the Ruff root cause precisely, so it is no longer left as an inference:

- `src/pancake_prediction/execution_intent.py:9:1`: `UP035` — import `Mapping` from `collections.abc`
- `src/pancake_prediction/stage5_evidence.py:11:1`: `UP035` — import `Mapping` from `collections.abc`

The final quality-gate step fails only because Ruff is unsuccessful. These two diagnostics are in pre-existing Stage 5 Python files; the External Intelligence commit itself adds Markdown/control metadata and does not introduce either lint location. This is case evidence, not a generalized rule: the import cleanup is directly recoverable from the current CI log and does not justify a separate Observation or Failure Memory entry.

## Repository-level invariants

`README.md` and PR #1 establish that accuracy/green infrastructure checks are insufficient for profitability claims and that the current transaction-capable path remains loopback/local-fork only with no signing authority in the research layer.
