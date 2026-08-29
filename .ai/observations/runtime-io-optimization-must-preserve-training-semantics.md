# Runtime I/O Optimization Must Not Shrink Model Training Semantics
Status: observation
Date: 2026-08-29
Source issue: https://github.com/Hibiki49aaiii/PancakeSwap_Prediction_AI/issues/7
Confidence: high

## Context

Stage 4 automatic Shadow inference operates inside a finite decision-to-lock window. The original runtime rebuilt a ClickHouse-backed research dataset over every replay epoch after a target became eligible, creating unnecessary market-data I/O in the time-critical path.

A tempting optimization is to train only on the last N historical rows. That is not equivalent to the current model.

## Observation

The current single-target Shadow model intentionally fits on every ResearchFeatureRow whose round:

1. is outside the target purge zone;
2. has a Bull/Bear settled outcome;
3. settled before the target decision timestamp;
4. has a feature row generated before the target decision.

Therefore a fixed recent-N slice changes the model and calibrator identity, probability, EV, and action. It must be treated as a model-specification change, not a performance optimization.

A safe runtime optimization preserves the complete canonical replay/history semantics and restricts only expensive market-data work to the exact eligible feature epochs plus the target epoch.

## Implementation pattern

- `required_shadow_feature_epochs()` derives the exact feature epoch plan from the same eligibility rule used by `build_shadow_inference()`.
- `build_chunked_clickhouse_research_dataset(..., required_epochs=...)` filters pool candidates before ClickHouse chunk grouping.
- Full canonical replay/events remain available while constructing prior-history and pool features.
- The runtime passes the exact target plan and keeps the final deadline guard unchanged.

## Evidence

Regression coverage requires:

- bounded ResearchFeatureRow values equal the corresponding full-builder rows;
- bounded ShadowInferenceResult equals the full-row inference result;
- target final outcome/final pool mutation does not change the required epoch plan or prediction;
- bounded ClickHouse path performs fewer chunk queries when non-required epochs occupy different chunks.

## Batch-vs-single-target boundary

The same principle applies to derived models that have both historical batch and live target use cases.

For pool projection, historical OOS evaluation legitimately computes projections for every target epoch. Stage 4 online inference needs exactly one target projection. Calling the full all-target builder in the live path preserves correctness but wastes decision-window time and can become quadratic as replay history grows.

The safe pattern is to extract a shared target-level implementation, then expose:

- an all-target wrapper for historical/OOS analysis;
- an exactly-one-target wrapper for online inference.

Both wrappers must produce an identical projection for the same target, including model identity and purge provenance.

## Why it matters

Performance work near a live decision cutoff can accidentally become hidden model drift. Separating model semantics from data-access cost keeps latency improvements reviewable and preserves historical OOS comparability.

## Applicability

- Stage 4 continuous Shadow runtime;
- any later online inference path that reuses historical research rows;
- feature cache/persistence work;
- model-serving optimizations where training/calibration membership is part of the research contract.

## Exceptions / Limitations

The bounded path is not constant-time as history grows because the current model still uses all eligible training rows. If this remains too slow, the next safe direction is a source-bound persistent cache of immutable historical ResearchFeatureRows, not an unreviewed training-history truncation.

## Related files

- `src/pancake_prediction/shadow_inference.py`
- `src/pancake_prediction/clickhouse_dataset.py`
- `src/pancake_prediction/shadow_runtime.py`
- `src/pancake_prediction/pool_projection.py`
- `tests/test_shadow_inference.py`
- `tests/test_clickhouse_dataset.py`
- `docs/ai/issues/7/IMPLEMENTATION_PLAN.md`
