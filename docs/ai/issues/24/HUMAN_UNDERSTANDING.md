# Issue #24 Human Understanding

## Problem

Issue #23 now has a Windows 11 operator runbook and a PowerShell feedback collector, but the repository does not currently execute those operator-facing assets on Windows in CI.

The existing Linux CI proves the Python codebase, but it cannot catch:

- PowerShell syntax/interpolation failures;
- Windows path and file-locking regressions;
- Windows editable-install failures;
- console-script entrypoint drift on Windows;
- accidental future dependence of the feedback collector on secret environment values.

That leaves a gap between "documented Windows procedure" and "mechanically proven Windows procedure."

## Solution

Add a Windows-only GitHub Actions workflow that validates the operator surface without pretending to be Stage 4 empirical Evidence.

The workflow should:

1. run on a Windows runner;
2. install Python 3.12;
3. install the repository with dev dependencies;
4. execute the three operator CLI entrypoints;
5. run the test suite on Windows;
6. invoke the PowerShell feedback collector with fixture bootstrap/preflight JSON;
7. inject a secret sentinel into `BSC_RPC_URL`;
8. assert that the generated report does not contain the sentinel.

## Important distinction

This workflow is portability/operator-tooling validation only.

It does not prove:

- real BSC source availability;
- real ClickHouse availability;
- prospective data collection;
- a successful Stage 4 campaign;
- predictive skill;
- profitability;
- funded-execution readiness.

## External infrastructure boundary

The Windows CI must not depend on Docker Desktop, a real BSC RPC, or a live ClickHouse instance.

Those remain real-host prerequisites for Issue #23 and are validated by the operator preflight on the actual Windows 11 machine.

## Safety

The workflow must not introduce or request:

- private keys;
- mnemonics;
- wallet unlock;
- transaction signing;
- mainnet broadcast;
- funded execution.

The secret-redaction test uses a synthetic sentinel only.
