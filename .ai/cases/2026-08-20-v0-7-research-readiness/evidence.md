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

`evidence/quality-gate.json` at the observed head records:

- mypy: success
- pytest: success, 294 passed
- Bandit: success
- pip-audit: success
- Ruff: failure
- ready: false

This case deliberately records no Ruff root cause because the persisted evidence only establishes the outcome.

## Repository-level invariants

`README.md` and PR #1 establish that accuracy/green infrastructure checks are insufficient for profitability claims and that the current transaction-capable path remains loopback/local-fork only with no signing authority in the research layer.
