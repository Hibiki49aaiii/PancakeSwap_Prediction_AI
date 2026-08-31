# Runtime retry taxonomy must separate integrity from transient failures

Status: observation / high
Date: 2026-08-29

A bounded retry loop is safe only when repeating the failed operation can plausibly recover without changing campaign/source assumptions.

## Retryable families

Examples that may recover between cycles:

- generic provider/RPC transport failures;
- generic ClickHouse transport/service failures;
- incomplete Binance live catch-up where partial retry-safe progress has already been persisted.

These can use a finite consecutive retry budget.

## Immediate-fatal families

Do not spend the retry budget on contradictions that another identical cycle cannot repair:

- canonical oracle proxy / Chainlink aggregator drift from the bound source route;
- prospective Binance lineage contamination by a non-live latest source;
- invalid configuration or malformed source/schema state represented by runtime `ValueError`.

## Type, do not parse messages

Source-integrity failures should have dedicated exception subclasses that preserve the existing base exception family.

The runtime should catch those dedicated subclasses before the broader retryable base class.

Do not decide retry safety by matching exception message text.

## Output privacy

Both retry and fatal terminal telemetry should expose the exception class, not `str(exc)`.

Raw provider messages may contain endpoints, tokens, credentials or request details.

## Semantic boundary

Retry classification, retry budget and retry interval are operational behavior. They do not belong in the immutable campaign semantic manifest or campaign Evidence.

## Revalidate against

- `src/pancake_prediction/shadow_chain_sync.py`
- `src/pancake_prediction/binance_live.py`
- `src/pancake_prediction/shadow_runtime_cli.py`
- Issue #20 tests
