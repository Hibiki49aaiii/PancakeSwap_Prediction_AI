# Issue #20 Implementation Plan

## Goal

Make continuous Stage 4 retry semantics match the documented fail-closed boundary: transient/provider/catch-up errors may retry within the bounded limit, while source-integrity and invalid-state failures terminate immediately.

## Current mismatch

Issue #19 catches `RpcError`, `BinanceLiveError`, `ClickHouseError`, and `ValueError` uniformly.

That means source route drift, live-lineage contamination, malformed runtime/source data, and retry-unsafe schema errors wait through the retry budget even though they cannot be repaired by another identical cycle.

## Typed classification

### Shadow chain route drift

Add:

`ShadowChainSourceIntegrityError(RpcError)`

Use it only when the currently proven Prediction oracle proxy / Chainlink aggregator differs from the canonical campaign anchor.

Transport/provider `RpcError` remains unchanged.

### Binance live lineage contamination

Add:

`BinanceLiveSourceIntegrityError(BinanceLiveError)`

Use it when live provenance exists but the latest lineage cursor is no longer the expected `binance-rest:<venue>` source.

Generic `BinanceLiveError`, including incomplete max-pages catch-up, remains retryable.

### ValueError

Within the runtime loop, `ValueError` represents validated configuration/source/data/schema assumptions that failed after startup validation. Treat it as fatal rather than retryable.

## CLI order

Inside the cycle loop:

1. catch fatal source/integrity errors first;
2. fail with terminal type-only CLI error;
3. catch retryable provider/catch-up errors second;
4. apply Issue #19 bounded retry.

Both terminal paths must never serialize raw exception messages.

## Tests

- source modules raise the dedicated subclasses at the intended integrity boundaries;
- generic errors remain their existing base types;
- continuous generic RpcError / ClickHouseError / BinanceLiveError retry;
- continuous ValueError fails immediately;
- dedicated source-integrity subclasses fail immediately;
- fatal path emits no retry telemetry and performs no sleep;
- once mode remains immediate failure;
- secrets in exception messages remain absent from stderr/stdout.

## Semantic boundary

Error classification is operational fail-closed behavior. It does not enter campaign manifest or campaign Evidence.

## Safety

No signing, broadcast, funding, credential issuance or profitability promotion.

## Base

- branch: `agent/v0.7-alpha-research`
- base SHA: `589e0a7c2336d5d3348edf4e6f58fe186451125a`
- 487 tests / 87% / CI #1360 green.
