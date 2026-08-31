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


# Implementation Result — 2026-08-29

Issue #18 implementation is complete.

## Implemented

- `shadow_preflight.py` now reuses:
  - `inspect_binance_live_coverage()`;
  - `latest_binance_live_cursor()`.
- `BinanceLineagePreflight` now exposes:
  - `live_row_count`;
  - `latest_aggregate_trade_id`;
  - `latest_source_name`;
  - `prospective_source_consistent`.
- Preflight preserves archive-only historical bootstrap:
  - lineage rows > 0;
  - live row count == 0;
  - latest source may be archive;
  - source-consistency check passes.
- Once live provenance exists:
  - a latest cursor is required;
  - latest source must equal `binance-rest:<venue>`;
  - otherwise the corresponding source-consistency check fails.
- Added readiness checks:
  - `spot_lineage_source_consistent`;
  - `perp_lineage_source_consistent`.
- `--no-perp` keeps Perp presence/source-consistency checks satisfied without querying Perp lineage state.
- Malformed live coverage or cursor state fails closed.
- The implementation remains read-only:
  - SELECT-only ClickHouse calls;
  - no runtime/lineage lock;
  - no ClickHouse insert;
  - no Shadow Ledger mutation.
- Tests cover:
  - archive-only Spot/Perp compatibility;
  - live Spot/Perp with live latest cursor;
  - live Spot advanced by archive;
  - live Perp advanced by archive;
  - malformed live cursor;
  - existing preflight ledger-compatibility and external-failure behavior.

## Files Changed

- `.ai/observations/prospective-observation-lineages-need-single-writer-coordination.md`
- `docs/STAGE4_SHADOW.md`
- `docs/ai/issues/18/HUMAN_UNDERSTANDING.md`
- `docs/ai/issues/18/IMPLEMENTATION_PLAN.md`
- `src/pancake_prediction/shadow_preflight.py`
- `tests/test_shadow_preflight.py`

## Verification

Production/test source SHA:
`d685a4cd2e7637ba5486c3728b4b13c12be042fc`

Quality Evidence #310 / run `33254016206`:

- pytest: **480 passed**
- coverage: **87%**
- Ruff: success
- mypy strict: success
- Bandit: success
- pip-audit: success
- final quality gate: success

Full PR CI #1344 / run `33254017960`:

- pytest: **480 passed in 25.82s**
- coverage: **87%**
- test: success
- ClickHouse integration: success
- Gitleaks: success
- pinned legacy **144,000-round** audit: success
- overall CI: success

## Post-Implementation Review

### Correctness

Preflight and normal runtime now share the same live-coverage/latest-cursor source-integrity helpers. A source state known to fail normal runtime is no longer reported as structurally ready.

### Bootstrap compatibility

Historical archive-only lineages remain valid before prospective collection starts. The stricter requirement activates only after live provenance exists.

### Read-only boundary

No process lock, ClickHouse write, source collection or Shadow Ledger mutation was introduced into preflight.

### Evidence/privacy

The preflight report exposes only lineage source provenance and counts. It does not serialize connection credentials.

### Safety

No signer, private key, mainnet transaction signing, broadcast, funded execution, credential issuance/change, profitability promotion or full-history promotion was introduced.

### Remaining boundary

The check evaluates current logical ClickHouse state. It does not recover or prove historical live rows already physically removed by prior replacement/merge history.
