# Issue #24 Implementation Plan

## Goal

Mechanically validate the Issue #23 Windows 11 operator surface before it is used on the real campaign host.

## Base

- branch: `agent/v0.7-alpha-research`
- base SHA: `050b6df0d882441baf65695883f58e5d93f3d640`
- Issue #23 remains the empirical campaign issue
- Linux source quality before this change: 516 tests / 87% coverage

## Architecture

Add a dedicated workflow:

`.github/workflows/windows-operator-smoke.yml`

The workflow runs on `windows-latest` and is intentionally separate from the Linux `ci.yml` jobs because its purpose is cross-platform operator validation rather than ClickHouse/BSC integration.

### Steps

1. Checkout.
2. Setup Python 3.12.
3. Upgrade pip and install `-e ".[dev]"`.
4. Run:
   - `pcs-prediction status`
   - `pcs-clickhouse --help`
   - `pcs-shadow-runtime --help`
5. Run Windows pytest.
6. Create minimal local fixture files:
   - `artifacts/recent-bootstrap.json`
   - `evidence/stage4-preflight.json`
7. Set `BSC_RPC_URL` to a unique synthetic sentinel.
8. Execute `scripts/windows_stage4_feedback.ps1`.
9. Assert:
   - output file exists;
   - collector says secret values were not collected;
   - sentinel value is absent.

## Trigger policy

Run on:

- manual workflow dispatch;
- pushes to `agent/v0.7-alpha-research` affecting Windows operator tooling, Python source/tests, or project metadata;
- pull requests affecting the same surfaces.

This keeps Windows drift visible without coupling Stage 4 empirical operation to GitHub-hosted infrastructure.

## Feedback collector behavior

No semantic Stage 4 changes are planned.

If Windows CI exposes a real PowerShell portability issue, apply the smallest fix in `scripts/windows_stage4_feedback.ps1` and record it here.

## Alternatives considered

### Add Windows as another matrix target in the main CI

Not selected for this issue. Main CI also contains Linux-specific ClickHouse service and legacy audit responsibilities. A dedicated workflow keeps the Windows operator concern explicit and independently inspectable.

### Run a real ClickHouse server on Windows CI

Rejected. The operator runbook requires Docker Desktop on the actual host; CI availability of Windows container/Linux-container virtualization is not a stable prerequisite and would obscure the intended portability check.

### Skip pytest and test only PowerShell

Rejected. The real host executes the Python package on Windows, so Windows-specific filesystem/runtime regressions matter.

## Risks and mitigations

- **Windows test failures**: treat them as real portability findings; do not suppress broadly.
- **Workflow cost/runtime**: one Windows job only.
- **Collector command failures for absent Docker/ClickHouse**: allowed inside the report; the workflow separately verifies the collector itself completes and redacts secrets.
- **False claim of Stage 4 readiness**: documentation and workflow naming keep this explicitly as operator smoke only.

## Pre-Implementation Review

### Architecture

Dedicated Windows workflow is the smallest boundary that proves operator portability without changing production Stage 4 logic.

### Failure / Security

The sentinel redaction assertion directly guards against a future regression where `BSC_RPC_URL` is dumped into operator feedback.

### Human understanding

A green Windows workflow means the packaged operator tooling runs on Windows. It does not replace the real-host preflight or the multi-day campaign required by Issue #23.

## Verification Plan

- exact-head Windows workflow result;
- existing Linux quality/CI result after implementation;
- inspect workflow log/artifact behavior;
- update Issue #24 with exact run IDs and any Windows-specific findings.


# Implementation Result — 2026-08-31

Issue #24 implementation is complete.

## Implemented

- Added `.github/workflows/windows-operator-smoke.yml`.
- Windows runner uses Python 3.12 and installs `-e ".[dev]"`.
- Windows workflow verifies:
  - `pcs-prediction status`;
  - `pcs-clickhouse --help`;
  - `pcs-shadow-runtime --help`;
  - the full pytest suite;
  - PowerShell feedback collector execution;
  - bootstrap/preflight fixture consumption;
  - feedback artifact creation;
  - synthetic `BSC_RPC_URL` sentinel does not appear in the report.
- Windows operator feedback was security-hardened so raw `docker info` output is no longer collected. Only Docker server version / OS type are requested.
- `docs/STAGE4_WINDOWS_11_RUNBOOK.md` now records the Windows portability evidence boundary.

## Verification

Relevant implementation/source SHA:

`b33e5e7db5bec7df2fb941fc35f972590a12b4d3`

Windows operator smoke PR run #2 / run `33341383606`:

- checkout: success
- Python 3.12 setup: success
- editable install: success
- operator CLI entrypoints: success
- Windows pytest: success
- fixture creation: success
- feedback collector smoke: success
- BSC RPC sentinel non-leak assertion: success
- feedback artifact upload: success
- overall workflow: success

Linux CI run `33341383620` on the same SHA:

- test / quality gate: success
- ClickHouse integration: success
- Gitleaks: success
- pinned 144,000-round audit: success
- overall CI: success

The first Windows runs on `8c71536e...` were cancelled by concurrency when the Docker-detail minimization fix advanced the branch. Their install/CLI steps had already passed; they are not used as final evidence.

## Post-Implementation Review

### Architecture

The Windows workflow remains separate from the Linux integration workflow and from Issue #23 empirical campaign Evidence. This avoids conflating portability with live-source readiness.

### Cross-platform result

The existing Python test suite passed on Windows without suppressions or Windows-specific skips introduced by this issue.

### Feedback privacy

The feedback collector does not read `BSC_RPC_URL`, and CI injects a synthetic sentinel and fails if it appears in the generated artifact. Raw `docker info` was additionally reduced to minimal server metadata to avoid unnecessary host/proxy/registry disclosure.

### Safety

No private key, mnemonic, signer, transaction signing, mainnet broadcast, funded execution, credential provisioning, profitability promotion, or Stage 6B capability was introduced.

### Remaining boundary

Issue #24 proves the Windows operator tooling can run. It does not prove the user's actual Windows host has a suitable BSC RPC, ClickHouse data lineage, or a successful Stage 4 preflight. Those remain Issue #23 empirical prerequisites.
