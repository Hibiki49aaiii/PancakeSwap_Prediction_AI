# Stage 2/3 validation contract

Stage 2 and Stage 3 are designed to fail closed on look-ahead.

## Decision clock

A round decision timestamp is derived from information known at round start: `start_timestamp + known_interval - decision_lead`. The observed `LockRound` transaction timestamp is never used to choose the decision cutoff. Inclusion latency is checked against the scheduled lock timestamp.

## Pool separation

Three pool states are deliberately separate:

1. observed pool: accepted `BetBull`/`BetBear` events strictly before the decision cutoff;
2. projected pool: a forecast generated no later than the decision cutoff, used for expected-value selection;
3. final pool: all accepted historical bets, used only to calculate counterfactual realized payout after the decision has already been fixed.

A projection below an already-observed side pool is invalid because accepted stake cannot disappear. Future-generated projections and signals raise an error rather than being silently accepted.

By default, a backtest requires a pre-decision pool projection. `observed-hold` is available only when the caller explicitly disables that requirement, and is a baseline rather than a claim that pool movement is zero.

## Economics

Trade selection uses stake-specific pari-mutuel expected value with treasury fee, own-stake dilution, bet gas, and win-only claim gas. Historical `RewardsCalculated` values, when available, must reconcile with the observed final pool and fee or the round is excluded.

## Walk-forward

Stage 3 uses expanding folds with explicit purge and embargo. Every OOS signal carries `train_max_epoch`; a signal is rejected if its training boundary violates the requested purge. Evaluation reports Brier score, Brier skill versus the empirical base rate, log loss, ECE-10, directional accuracy, and Wilson 95% confidence interval.
