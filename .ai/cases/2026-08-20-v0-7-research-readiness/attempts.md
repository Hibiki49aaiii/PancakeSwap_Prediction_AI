# Attempts

## Public BSC endpoint discovery

The repository implemented `public-research-input-probe` to test plausible public BSC sources instead of assuming archive capability. The tested candidates did not satisfy the exact historical-state gate; failure modes are preserved in `evidence/public-research-input-probe.json`.

This is worth retaining because repeating endpoint selection from provider reputation/reachability alone would recreate the same dead end.

## Authenticated archive path

A dedicated `BSC_ARCHIVE_RPC_URL` preflight path and historical-bootstrap workflow were prepared. The current redacted evidence shows the gate is not configured, so no historical campaign should be claimed as completed from that path.

## Authenticated recent Alchemy path

On 2026-08-31 an authenticated BSC mainnet endpoint was configured locally without persisting its credential. Chain/head calls and 1- to 10-block `eth_getLogs` queries succeeded. Larger log ranges returned HTTP 400 with JSON-RPC `-32600`, explicitly identifying an Alchemy Free tier maximum of 10 blocks.

The RPC client had treated that response as a generic transport error, so the collector could not recognize the range limit and adapt. The client now preserves standard JSON-RPC errors carried in non-429 HTTP error bodies. A real 15-minute, 2,000-block Prediction + Chainlink bootstrap then completed through automatic splitting with 130 Prediction events, 27 Chainlink events, four replay rounds, and no duplicate canonical heights.

That bounded success does not unblock the three-day campaign: the observed request shape projects to roughly 30 hours and more than 500,000 RPC requests. A wider-range provider tier or equivalent log-capable source must be re-probed before spending that quota.

## What is intentionally not recorded

Routine command failures, transient tool/environment issues, and implementation details that are obvious from source/Git are excluded. They do not meet the future decision-changing value threshold.
