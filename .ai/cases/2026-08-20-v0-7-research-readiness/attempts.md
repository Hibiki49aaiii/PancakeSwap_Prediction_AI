# Attempts

## Public BSC endpoint discovery

The repository implemented `public-research-input-probe` to test plausible public BSC sources instead of assuming archive capability. The tested candidates did not satisfy the exact historical-state gate; failure modes are preserved in `evidence/public-research-input-probe.json`.

This is worth retaining because repeating endpoint selection from provider reputation/reachability alone would recreate the same dead end.

## Authenticated archive path

A dedicated `BSC_ARCHIVE_RPC_URL` preflight path and historical-bootstrap workflow were prepared. The current redacted evidence shows the gate is not configured, so no historical campaign should be claimed as completed from that path.

## What is intentionally not recorded

Routine command failures, transient tool/environment issues, and implementation details that are obvious from source/Git are excluded. They do not meet the future decision-changing value threshold.
