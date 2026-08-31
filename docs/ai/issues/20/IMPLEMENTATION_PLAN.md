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


# Implementation Result — 2026-08-29

Issue #20 implementation is complete.

## Implemented

- Added `ShadowChainSourceIntegrityError(RpcError)`.
- Oracle proxy / Chainlink aggregator drift from the canonical route anchor now raises the dedicated source-integrity subtype.
- Added `BinanceLiveSourceIntegrityError(BinanceLiveError)`.
- A prospective Binance lineage whose latest cursor was replaced by a non-live source now raises the dedicated source-integrity subtype.
- Generic Binance max-pages catch-up exhaustion remains ordinary `BinanceLiveError` and therefore remains bounded-retry eligible.
- Continuous runtime catch ordering is now:
  1. fatal `BinanceLiveSourceIntegrityError`, `ShadowChainSourceIntegrityError`, and `ValueError`;
  2. retryable generic `BinanceLiveError`, `ClickHouseError`, and `RpcError`.
- Fatal cycle errors:
  - exit 2 immediately;
  - emit no `cycle_error_retry` JSON;
  - perform no retry sleep;
  - expose only the exception class in terminal CLI output.
- `--once` remains immediate-failure for all supported cycle errors.
- Retry budget, poll interval and classification remain operational and excluded from campaign manifest/Evidence identity.

## Files Changed

- `.ai/index.md`
- `.ai/observations/runtime-retry-taxonomy-must-separate-integrity-from-transient-failures.md`
- `docs/STAGE4_SHADOW.md`
- `docs/ai/issues/20/HUMAN_UNDERSTANDING.md`
- `docs/ai/issues/20/IMPLEMENTATION_PLAN.md`
- `src/pancake_prediction/binance_live.py`
- `src/pancake_prediction/shadow_chain_sync.py`
- `src/pancake_prediction/shadow_runtime_cli.py`
- `tests/test_binance_live.py`
- `tests/test_shadow_chain_sync.py`
- `tests/test_shadow_runtime_cli.py`

## Verification

Production/test source SHA:
`0cc50a26767e5d0be7884590d9f53b58d8450f69`

Quality Evidence #321 / run `33255268771`:

- pytest: **491 passed**
- coverage: **87%**
- Ruff: success
- mypy strict: success
- Bandit: success
- pip-audit: success
- final quality gate: success

Full PR CI #1370 / run `33255270111`:

- pytest: **491 passed in 26.01s**
- coverage: **87%**
- test: success
- ClickHouse integration: success
- Gitleaks: success
- pinned legacy **144,000-round** audit: success
- overall CI: success

## Implementation correction from quality review

The first integrated source/test version passed mypy, pytest (**491 tests**), Bandit and pip-audit but Ruff reported one obsolete `RpcError` import in `tests/test_shadow_chain_sync.py` after the route-drift assertion moved to the dedicated subclass.

The unused import was removed. No production behavior changed.

## Post-Implementation Review

### Fail-closed semantics

Source-integrity contradictions now terminate on the first observed failure rather than waiting through the operational retry budget.

### Recovery semantics

Potentially recoverable generic provider/service errors and incomplete Binance catch-up continue to use Issue #19's finite consecutive retry budget.

### Type safety

Safety no longer depends on exception-message wording. Dedicated integrity subclasses preserve the original base exception family for compatible callers.

### Privacy

Both retry and fatal terminal paths expose exception type only, not raw exception messages.

### Semantic identity

Retry classification is operational process behavior and does not modify the immutable campaign manifest or campaign Evidence.

### Safety

No private key, signer, transaction signing, mainnet broadcast, funded execution, credential issuance/change, profitability promotion or full-history promotion was introduced.

### Scope boundary

ClickHouse HTTP-status taxonomy, adaptive backoff and external/distributed supervision remain separate work.
