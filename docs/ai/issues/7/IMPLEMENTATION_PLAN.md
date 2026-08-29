# Issue #7 Implementation Plan

## Goal

Stage 4 continuous Shadow runtime の single-target inference で、モデル意味論を変えずに不要な ClickHouse I/O を削減する。

## Confirmed invariants

- research/model path は signer / private key / funded mainnet execution を持たない
- target の final outcome / final pool / close price / later settlement は decision input に含めない
- purge boundary は `ShadowInferenceConfig.purge_rounds` が source of truth
- model fit は target decision 時点までに settled している eligible feature rows をすべて使う
- calibration tail は eligible training rows の末尾
- pool projection は replay/events から独立に past-only training を行う
- no-target / source-warmup cycle は heavy dataset build を行わない
- deadline 到達後は prediction を ledger に append しない

## Why not a fixed recent training window

Current `build_shadow_inference()` does not use a fixed-size model training window. It fits over every eligible research row that:

1. is at or before `target_epoch - purge_rounds - 1`;
2. has a Bull/Bear settled label;
3. has settled before the target decision timestamp;
4. has a research feature row generated before the target decision.

Changing this to last-N rows would change the trained model, calibrator identity, raw/calibrated probability, and therefore EV/action. That is a model change, not a runtime optimization, so it is out of scope.

## Adopted design

### 1. Shared exact epoch plan

`required_shadow_feature_epochs()` in `shadow_inference.py` computes the exact feature epochs required by current inference semantics:

- all eligible training epochs under the same purge/settlement cutoff;
- target epoch.

The eligible-training record filter is shared with `build_shadow_inference()` to prevent specification drift.

### 2. Bounded ClickHouse dataset

`build_chunked_clickhouse_research_dataset(..., required_epochs=...)`:

- still builds pool-history semantics from the full canonical replay/events;
- filters pool feature candidates to the required epoch set before grouping ClickHouse time chunks;
- queries only chunks containing required epochs;
- constructs alpha/research rows only for required epochs;
- records requested epoch count/min/max in the build report.

This preserves prior-history features because the canonical replay remains complete. Only expensive market-data I/O and per-epoch alpha construction are bounded.

### 3. Runtime integration

When an eligible target exists:

1. compute exact required epochs;
2. pass the plan to the ClickHouse builder;
3. run unchanged `build_shadow_inference()`;
4. perform existing final deadline check;
5. append only if still timely.

No target or incomplete prospective source warmup still exits before dataset construction.

## Verification strategy

### Feature equivalence

For a deterministic fixture:

- build full ClickHouse dataset;
- build bounded dataset with one required epoch;
- assert target `ResearchFeatureRow` equality;
- assert fewer loaded chunks/queries when epochs occupy separate chunks.

### Inference equivalence

- build inference from all feature rows;
- build inference from only `required_shadow_feature_epochs()`;
- assert full `ShadowInferenceResult` equality, including model IDs, calibrator, projection, probabilities, EV and action.

### Leakage regression

- mutate target final label/close price/final pool;
- required epoch plan and inference must remain unchanged.

### Runtime binding

- verify runtime passes the exact required epoch plan into the ClickHouse builder;
- preserve source-warmup/no-target/deadline behavior.

## Expected impact

The optimization does **not** guarantee a fixed upper bound independent of historical campaign size, because the current model intentionally trains on all eligible historical rows.

It does eliminate:

- future/current non-target epoch market-data queries;
- purge-zone epoch market-data queries;
- unsettled epoch market-data queries;
- feature construction for epochs that cannot participate in this target decision.

A future optimization can persist/cache immutable historical feature rows, but that is a separate architectural change and should be evaluated after measuring this bounded path.

## Files

Expected/actual:

- `src/pancake_prediction/shadow_inference.py`
- `src/pancake_prediction/clickhouse_dataset.py`
- `src/pancake_prediction/shadow_runtime.py`
- `tests/test_shadow_inference.py`
- `tests/test_clickhouse_dataset.py`
- `tests/test_shadow_runtime.py`
- `docs/STAGE4_SHADOW.md`
- `README.md` only if status wording materially changes
- `.ai/` reusable learning if warranted

## Verification gates

- Ruff
- mypy strict
- pytest + coverage
- Bandit
- pip-audit
- ClickHouse integration
- Gitleaks
- existing legacy audit

## Safety status

This Issue does not introduce or authorize:

- wallet access
- private keys
- transaction signing
- mainnet broadcast
- funded execution
- profitability promotion
