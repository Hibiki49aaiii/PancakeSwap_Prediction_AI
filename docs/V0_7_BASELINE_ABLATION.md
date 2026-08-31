# v0.7 Purged Baseline and Feature Ablation

## Purpose

Before adding gradient boosting, neural networks, LLM-directed models, or more complex ensembles,
the project requires a deterministic baseline that is easy to audit and hard to leak future data into.

The v0.7 baseline is a dependency-free standardized logistic model followed by time-separated
probability calibration. It is not a profitability claim and it has no signing authority.

## Research feature families

The baseline groups features into four families:

- `settlement_source`: oracle age, Binance spot/perpetual versus Chainlink gaps, spot/perpetual
  basis, and empirical Chainlink update hazard;
- `cex_flow`: aggressive Binance spot and perpetual order-flow imbalance;
- `pool_state`: pre-decision Pancake pool share, pool size, recent pool-flow imbalance, bet count,
  and unique bettors;
- `round_history`: only previously settled round base-rate and volatility context.

`build_research_feature_row()` requires the Alpha and Pancake pool rows to refer to the same market,
epoch, and exact decision cutoff. A mismatch is rejected rather than aligned approximately.

## Training discipline

Each expanding walk-forward fold applies purge and embargo. Within each training fold, the final
chronological `calibration_rounds` are held out from logistic fitting and used only to fit the
histogram reliability calibrator. Test-round predictions therefore use:

1. scaler statistics computed from the fit portion only;
2. logistic coefficients fit on the fit portion only;
3. calibration learned from later-but-still-training-only held-out rows;
4. no target/test labels from the predicted fold.

Missing feature values are imputed to the training mean after standardization, which is zero. No
statistics from the test fold are used for imputation or scaling.

## Acceptance metrics

The baseline reports the existing Stage 3 OOS metrics:

- Brier score;
- Brier skill score versus the OOS empirical base rate;
- log loss;
- ECE-10;
- directional accuracy and Wilson 95% interval.

These metrics are necessary but insufficient. Positive probability metrics do not imply positive
PancakeSwap returns.

## Ablation

`run_feature_family_ablation()` runs the full feature set and one variant with each feature family
removed. A claimed alpha family is not accepted merely because a fitted coefficient is non-zero.
It must demonstrate stable OOS contribution when the family is removed.

The current ablation output is probability-only. The next integration step is to bind each OOS
signal to an independently generated, pre-decision pool projection and the Stage 2 cost-aware EV
engine. Only then can an ablation result include realized/counterfactual net EV.

## Promotion gate

A richer model is not promoted unless it beats this baseline on purged walk-forward data, remains
well calibrated, has stable regime/time performance, survives feature-family ablation, and produces
positive cost-aware EV after Pancake fee, own-stake dilution, gas, pool movement, and latency.
