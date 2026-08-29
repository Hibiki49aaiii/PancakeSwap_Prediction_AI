# Issue #8 Implementation Plan

## Goal

Stage 4 single-target Shadow inference の pool projection を、full OOS campaign 全target生成から切り離す。

## Existing behavior

`build_shadow_inference()` needs exactly one `PoolProjection` for the live target epoch, but previously called:

`build_oos_pool_projections(replay, events, config)`

and selected `projections[target_epoch]`.

The full builder loops over every replay target and scans eligible prior rounds for each target. That work is appropriate for historical OOS evaluation, but unnecessary in a live single-target decision window.

## Invariants

The single-target path must preserve exactly:

- target pre-lock decision snapshot;
- observed Bull/Bear pool at the cutoff;
- purge boundary;
- prior settlement-before-decision condition;
- `min_train_rounds`;
- trailing `window_rounds`;
- median Bull/Bear growth estimator;
- projection `model_id`;
- `train_max_epoch`;
- no dependence on target final pool/outcome.

## Adopted design

Refactor the existing implementation into one shared internal function:

`_projection_for_target(...)`

Both APIs call this same function:

- `build_oos_pool_projections()` for historical/research all-target output;
- `build_oos_pool_projection_for_target()` for Stage 4 live inference.

The public single-target API validates that the requested target epoch appears exactly once.

## Event-index optimization

The existing `_growth_target()` rebuilt decision-event indexing indirectly when evaluating each prior round. It now accepts a prebuilt `BacktestEventIndex`, shared from the caller.

This preserves the same snapshots while avoiding repeated event-index construction.

## Verification

- full builder target result equals single-target result;
- model_id and train_max_epoch identical;
- changing target final pool cannot change single-target projection;
- missing/duplicate target fails closed;
- existing Shadow inference leakage tests remain green;
- full quality and CI gates remain green.

## Out of scope

- projection estimator/model change;
- fixed-N model history;
- persistent feature cache;
- funded execution;
- campaign gate changes.
