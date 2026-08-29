# Issue #19 Implementation Plan

## Goal

Allow the continuous Stage 4 Shadow runtime to survive a bounded number of consecutive cycle failures without weakening fail-closed source/model semantics.

## Current behavior

`_run_once()` catches all supported cycle exceptions and immediately calls `parser.error()`.

That is correct for `--once`, but operationally fragile for a multi-day continuous campaign.

## Error policy

Supported cycle failures:

- `RpcError`;
- `BinanceLiveError`;
- `ClickHouseError`;
- `ValueError`.

Because these types currently mix transient transport failures and persistent source/configuration failures, the runtime will not assume they are indefinitely retryable.

### Once mode

One failure -> CLI exit 2.

### Continuous mode

- increment consecutive failure count;
- emit a redacted structured retry status;
- sleep the existing poll interval;
- retry while count < configured maximum;
- successful cycle resets count to zero;
- reaching the maximum -> CLI exit 2.

Default max = 5.

## Privacy

Do not serialize `str(exc)` into retry status or terminal message.

Expose only the exception class name.

This avoids leaking provider URLs, path-embedded API keys, auth errors or other external details.

## Lock semantics

Retry happens inside the already-acquired:

- Shadow campaign process lock;
- Spot lineage lock;
- optional Perp lineage lock.

This prevents another local writer from interleaving between failed attempts.

## Evidence semantics

Successful cycle output, `--evidence-output`, campaign latest and campaign last-success behavior remain unchanged.

Retry status is stdout operational telemetry only and is not written as campaign Evidence.

## Configuration

Add `--max-consecutive-cycle-errors`, default 5, minimum 1.

This is an operational recovery setting and must not enter:

- `ShadowRuntimeConfig`;
- campaign manifest;
- campaign Evidence.

## Tests

- once error exits immediately;
- continuous one failure then success;
- successful cycle resets error counter;
- max consecutive failures exits 2;
- retry JSON has only error type, not message;
- KeyboardInterrupt during retry sleep exits 0;
- lock contention remains blocked during retry;
- parser validates max >= 1;
- existing successful runtime/preflight paths remain unchanged.

## Verification

- Ruff
- mypy strict
- pytest + coverage
- Bandit
- pip-audit
- Gitleaks
- ClickHouse integration
- pinned 144,000-round audit

## Base

- branch: `agent/v0.7-alpha-research`
- base SHA: `6ff878e3a1575a1f4f9c0e4917384f581be77d3e`
- 480 tests / 87% / CI #1348 green.
