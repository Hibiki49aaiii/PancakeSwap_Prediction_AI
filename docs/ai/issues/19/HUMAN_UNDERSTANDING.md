# Issue #19 Human Understanding

## Problem

A week-long Shadow campaign should not stop because one external request failed once.

But the current exception classes do not cleanly separate temporary provider failures from permanent source-integrity/configuration failures.

Retrying forever would hide a real fault.

## Safe compromise

Continuous mode retries only a bounded number of consecutive failed cycles.

A success proves the runtime recovered and resets the counter.

If failures keep happening, the runtime exits instead of silently looping forever.

## Once mode

`--once` remains deterministic and strict: any cycle error exits immediately.

## Privacy

Retry status reports the exception type but not its message. Provider error text can contain sensitive endpoint or authentication details.

## Locks

The runtime keeps campaign and Binance lineage ownership while retrying, so another process cannot interleave source collection between attempts.
