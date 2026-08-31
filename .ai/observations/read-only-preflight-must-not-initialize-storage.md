# Read-only preflight must not initialize storage

Status: observation / high
Date: 2026-08-29

A Stage 4 campaign-start preflight is only trustworthy if the preflight itself cannot create or mutate the state that it is supposed to validate.

## Rule

For a read-only preflight:

- check that an expected SQLite database file already exists **before** opening it through helpers that may initialize schema or create parent directories;
- do not call collector/bootstrap/sync paths that write canonical chain data;
- do not initialize or append to the Shadow Ledger;
- do not persist probed Binance rows into ClickHouse;
- do not update cycle or campaign Evidence as a side effect of validation;
- reuse the real runtime configuration so the preflight does not become a second, drifting configuration model.

## Inspecting existing runtime state

If preflight must inspect an existing runtime database, do not reuse a normal connection helper whose setup has write-side effects.

For the Shadow Ledger specifically:

- the runtime connection enables SQLite WAL mode and is therefore not the correct preflight primitive;
- use a SQLite URI `mode=ro` connection with query-only semantics;
- do not run schema initialization or additive migrations;
- treat a missing database as a distinct "new campaign" state rather than creating it;
- distinguish an empty unbound legacy ledger from event-bearing unbound history;
- compare an existing immutable campaign manifest against the exact manifest the runtime would bind;
- malformed schema/manifest state must fail closed without serializing raw database/provider errors.

Read-only compatibility inspection should prove that the selected existing state can be safely continued, not repair it into compatibility.

## Readiness semantics

A green preflight means only structural campaign-start readiness:

- required existing state can be read;
- configured historical capacity lower bounds are present;
- source anchors are syntactically present;
- expected BSC/ClickHouse/Binance read paths are reachable and structurally compatible.

It does **not** prove:

- the live oracle route still matches the stored route anchor;
- prospective live-flow warmup has completed;
- a current target is inferable;
- complete historical feature coverage;
- profitability;
- signing or funded execution readiness.

Those properties remain owned by the normal runtime, campaign Evidence, or later explicitly authorized stages.

## Why this matters

Calling a schema initializer during a preflight can turn “missing database” into “empty database,” obscuring the original failure and violating the meaning of read-only validation. Likewise, importing write-capable sync/ingest paths into preflight makes a green result ambiguous because the validation itself may have repaired or altered the state.

## Revalidate against

- `src/pancake_prediction/shadow_preflight.py`
- `src/pancake_prediction/shadow_runtime_cli.py`
- `docs/STAGE4_SHADOW.md`
- Issue #11 tests
- Issue #13 tests
