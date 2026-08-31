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


# Implementation Result — 2026-08-29

Issue #19 implementation is complete.

## Implemented

- Added operational CLI option:
  - `--max-consecutive-cycle-errors`;
  - default: `5`;
  - minimum: `1`.
- Removed `parser.error()` from the cycle wrapper so main loop can choose one-shot vs continuous failure policy.
- `--once` remains fail-fast:
  - first supported cycle error exits 2;
  - no retry sleep;
  - terminal CLI error includes only the exception class, never `str(exc)`.
- Continuous mode now:
  - tracks consecutive supported cycle errors;
  - retries while the count is below the configured maximum;
  - resets the counter after every successful cycle;
  - exits 2 when the maximum is reached.
- Retry status is compact JSON with:
  - `status=cycle_error_retry`;
  - `error_type`;
  - consecutive/max counters;
  - retry interval;
  - signing/broadcast/funded/profitability safety fields.
- Raw exception messages are never included in retry telemetry or the terminal cycle-error message.
- Retry sleep reuses the configured `--poll-seconds`.
- `KeyboardInterrupt` during retry sleep returns 0.
- Retries execute inside the already-held:
  - Shadow campaign process lock;
  - Spot lineage lock;
  - optional Perp lineage lock.
- Successful cycle output and Evidence checkpoint behavior remain unchanged.
- Retry status itself is not written to runtime/campaign Evidence.
- The retry-limit option is intentionally excluded from:
  - `ShadowRuntimeConfig`;
  - campaign manifest;
  - campaign Evidence.
- Regression tests cover:
  - once-mode immediate failure;
  - transient error followed by recovery;
  - lock ownership across retry;
  - successful-cycle counter reset;
  - maximum consecutive failures;
  - secret/raw-error redaction;
  - KeyboardInterrupt during retry sleep;
  - invalid max setting;
  - operational retry setting not changing runtime semantic config.

## Files Changed

- `.ai/index.md`
- `.ai/observations/continuous-runtime-retries-must-be-bounded-and-redacted.md`
- `docs/STAGE4_SHADOW.md`
- `docs/ai/issues/19/HUMAN_UNDERSTANDING.md`
- `docs/ai/issues/19/IMPLEMENTATION_PLAN.md`
- `src/pancake_prediction/shadow_runtime_cli.py`
- `tests/test_shadow_runtime_cli.py`

## Verification

Production/test source SHA:
`f0ebd85db915eb72ae7a023a28146e1ceb07fc88`

Quality Evidence #314 / run `33254590381`:

- pytest: **487 passed**
- coverage: **87%**
- Ruff: success
- mypy strict: success
- Bandit: success
- pip-audit: success
- final quality gate: success

Full PR CI #1356 / run `33254592678`:

- pytest: **487 passed in 23.81s**
- coverage: **87%**
- test: success
- ClickHouse integration: success
- Gitleaks: success
- pinned legacy **144,000-round** audit: success
- overall CI: success

## Implementation corrections from quality review

The first integrated retry-test version had five mypy `attr-defined` errors because tests patched `shadow_runtime_cli.time` as an implicitly exported module attribute. The production retry implementation and all 487 runtime tests were otherwise green.

Tests were corrected to patch the fully-qualified `pancake_prediction.shadow_runtime_cli.time.sleep` target. One remaining multiline occurrence was then corrected. No production retry semantics were weakened.

## Post-Implementation Review

### Reliability

A single external/provider cycle failure no longer kills a continuous multi-day campaign process. Consecutive persistent failures still terminate after a finite configured bound.

### Fail-closed boundary

The exception taxonomy currently mixes transient and persistent failures. Bounded retry is therefore deliberately finite rather than assuming every supported exception is indefinitely recoverable.

### Coordination

Campaign and Binance lineage locks remain held across retries, preventing another local writer from interleaving source collection between attempts.

### Privacy

Retry and terminal cycle-error output expose only the exception class, never the raw exception message.

### Semantic identity

Retry limit and polling interval remain operational process behavior and do not modify campaign semantic identity or Evidence.

### Safety

No private key, signer, mainnet transaction signing, live broadcast, funded execution, credential issuance/change, profitability promotion, or full-history promotion was introduced.

### Scope boundary

External service supervision, distributed failover, and a finer transient-vs-fatal exception taxonomy remain separate work.
