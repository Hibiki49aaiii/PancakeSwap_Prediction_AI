# Issue #18 Implementation Plan

## Goal

Make Stage 4 read-only preflight detect the same prospective Binance lineage source-integrity violation that normal live sync already rejects.

## Existing gap

Preflight summarizes configured ClickHouse lineage size and time bounds but does not inspect live provenance or the latest cursor source.

Normal runtime already rejects:

- prospective live coverage exists; and
- the latest lineage cursor was advanced by a non-live source.

Therefore a structurally green preflight can currently fail immediately when normal runtime begins.

## Design

Reuse the existing canonical helpers from `binance_live.py`:

- `inspect_binance_live_coverage()`;
- `latest_binance_live_cursor()`.

Do not duplicate source-provenance semantics in preflight.

### BinanceLineagePreflight

Extend the report with:

- `live_row_count`;
- `latest_aggregate_trade_id`;
- `latest_source_name`;
- `prospective_source_consistent`.

### Consistency rule

For a configured lineage:

- no rows -> ordinary lineage-presence failure;
- archive-only history, live_row_count == 0 -> source consistent;
- live_row_count > 0 -> latest cursor must exist and use `binance-rest:<venue>`;
- malformed coverage/cursor -> lineage/source check fails closed.

### Checks

Add:

- `spot_lineage_source_consistent`;
- `perp_lineage_source_consistent`.

When Perp is disabled, both Perp presence and source-consistency checks are true.

## Read-only boundary

The new checks use only ClickHouse SELECT queries.

Preflight still:

- creates no runtime/lineage lock;
- inserts no ClickHouse row;
- writes no Shadow Ledger state.

## Tests

Update the preflight fake ClickHouse source to model:

- archive-only lineage;
- live provenance + live latest cursor;
- live provenance + archive latest cursor;
- malformed live coverage/cursor.

Verify overall `ready`, individual checks and serialized report.

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
- base SHA: `deac4bc68ce00610cec68ec68c7ed404c4385e35`
- 476 tests / 87% / CI #1340 green.
