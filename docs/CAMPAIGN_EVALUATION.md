# Source-Bound Economic Campaign Evaluation

`pcs-clickhouse campaign-evaluate` is the v0.7 bridge from a checksum-verified, source-bound research dataset to purged out-of-sample probability and economic validation.

It does **not** authorize signing or live broadcast. The command is research-only and consumes the same canonical SQLite Prediction/active-Chainlink history and bounded ClickHouse Binance windows used by `dataset-summary`.

## Binding model

The command first rebuilds the canonical dataset bundle and deterministic ClickHouse campaign manifest. The manifest binds:

- replay input/output digests,
- canonical Prediction event count,
- active Chainlink oracle history,
- Spot/Perpetual timestamp-unit assumptions,
- Binance availability-lag assumptions,
- Chainlink availability lag,
- feature cutoff/lookback/age assumptions,
- exact ClickHouse query envelope,
- logical `FINAL` source slices and their archive SHA-256 values.

The resulting `campaign_digest` is passed into the economic evaluator. The evaluator then creates an `evaluation_digest` that additionally binds stake, bet gas, claim gas, inclusion latency, walk-forward/purge/calibration settings, pool-projection settings, and the resulting metrics.

Changing any bound cost/latency setting changes the evaluation digest.

## Required economic inputs

The following arguments are mandatory. There are intentionally no silent zero-cost defaults for them:

- `--stake-wei`
- `--bet-gas-wei`
- `--claim-gas-wei`
- `--inclusion-latency-seconds`

`--feature-lead-seconds` is shared by feature construction and economic decision timing. The evaluator therefore cannot silently score a feature matrix built at one cutoff using a different decision cutoff.

## Evaluation layers

The evaluator runs:

1. deterministic expanding-window purged/embargoed OOS baseline fitting,
2. held-out probability calibration,
3. independent purged post-decision pool-growth projection,
4. cost-aware pari-mutuel economic backtest using only the OOS direction signal plus independent pool projection,
5. optional economic feature-family ablation with `--run-ablation`.

The economic backtest includes the configured treasury fee, own stake in the pool, projected post-decision pool movement, explicit bet/claim gas, and inclusion latency. Final target-round Bull/Bear pools are not used as decision features or as the pool projection for that same round.

## Example

The latency and gas values below are placeholders for a scenario. They are **not** measured production values and must not be cited as evidence of profitability.

```bash
pcs-clickhouse campaign-evaluate \
  --market BNBUSD \
  --db artifacts/bnbusd-history.sqlite \
  --spot-timestamp-unit auto \
  --spot-availability-lag-ms 25 \
  --perp-timestamp-unit milliseconds \
  --perp-availability-lag-ms 40 \
  --chainlink-availability-lag-ms 500 \
  --feature-lead-seconds 20 \
  --stake-wei 1000000000000000 \
  --bet-gas-wei 120000000000000 \
  --claim-gas-wei 90000000000000 \
  --inclusion-latency-seconds 3 \
  --min-train-rounds 2000 \
  --test-rounds 500 \
  --purge-rounds 2 \
  --embargo-rounds 2 \
  --calibration-rounds 500 \
  --pool-min-train-rounds 500 \
  --pool-window-rounds 5000 \
  --run-ablation
```

## Output contract

The JSON output contains four top-level evidence blocks:

- `inputs`: canonical replay/oracle evidence,
- `assumptions`: dataset timing and source assumptions,
- `campaign_manifest`: source-bound manifest and `campaign_digest`,
- `evaluation`: OOS/calibration/economic result and `evaluation_digest`.

The evaluation report includes probability metrics, fold/calibration counts, direction-signal count, pool-projection count, joint eligible epoch count, a summarized backtest, and optional economic ablation. It intentionally does not dump the full trade ledger or feature matrix to stdout.

## Profitability gate

A positive result from one campaign is insufficient. Before any profitability claim, the same source period must be evaluated across multiple plausible positive-lag scenarios and cost assumptions, then checked for stability across time/regime and by feature-family ablation. Shadow results with measured end-to-end latency should replace assumed latency distributions as soon as they exist.

Green CI proves implementation consistency only. It is not evidence that the strategy is profitable.
