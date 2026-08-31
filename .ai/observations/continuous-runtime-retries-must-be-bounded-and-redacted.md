# Continuous runtime retries must be bounded and redacted

Status: observation / high
Date: 2026-08-29

A long-running prospective campaign should tolerate isolated external failures without turning every provider hiccup into a process stop.

At the same time, broad exception classes may contain both transient transport failures and persistent source-integrity or configuration failures. Retrying them forever would hide a real fault.

## Rule

For a continuous runtime:

- retry supported cycle errors only for a bounded number of consecutive failures;
- reset the consecutive counter after any successful cycle;
- fail closed when the configured consecutive-error limit is reached;
- keep one-shot mode strict and deterministic: one cycle error means immediate failure.

## Lock ownership

Retries should occur while the runtime still owns all process/source coordination locks.

Releasing locks between failed attempts permits another local writer to interleave source collection and changes the recovery sequence.

For Stage 4 this means retaining:

- the Shadow campaign process lock;
- the Spot Binance lineage lock;
- the Perp lineage lock when enabled.

## Telemetry privacy

Operational retry telemetry should expose the error class, not the raw exception message.

Provider exception text can contain:

- credential-bearing URLs;
- tokens or auth details;
- local paths;
- backend response content.

Structured retry output should therefore report counters, retry interval and error type only.

## Semantic boundary

Retry limits and retry intervals are operational recovery policy.

Do not bind them into:

- model/inference configuration;
- campaign manifest identity;
- campaign Evidence;
- profitability or historical-completeness gates.

A retry does not create campaign Evidence by itself. Only successful cycle behavior should update the existing runtime/campaign Evidence outputs.

## Revalidate against

- `src/pancake_prediction/shadow_runtime_cli.py`
- Issue #19 tests
