# One-Day Economic Robustness Is Not Alpha Proof
Status: observation
Date: 2026-08-22
Source case: `../cases/2026-08-20-v0-7-research-readiness/`
Confidence: high

## Context

The Aug 18–19 BNBUSD source-bound economic smoke was expanded into an eight-scenario economic sensitivity run plus a five-variant feature-family ablation. The robustness gate intentionally checks non-vacuous OOS execution and source binding rather than requiring positive PnL.

## Observation

A bounded sensitivity/ablation run can show that the economic plumbing survives cost, latency, stake, and EV-threshold perturbations without establishing predictive alpha or feature necessity.

For the persisted one-day robustness evidence:

- all 8 configured economic scenarios produced positive PnL;
- worst-case PnL was `19007678140802798` wei in `combined-stress`;
- minimum ROI was `25343` ppm;
- the baseline produced `85367433627531589` wei PnL and `55433` ppm ROI;
- the probability model still had Brier skill score `-0.12435269318908326` on only 159 scored OOS samples;
- every economic scenario reused the same probability predictions, so positive economic sensitivity does not repair negative probability skill.

Feature-family ablation also failed to establish that the full feature set was uniquely best:

- `full-v1`: Brier `0.2770743751658553`, Brier skill `-0.12435269318908326`, PnL `85367433627531589` wei;
- without `round_history`: Brier `0.2760876735773585`, Brier skill `-0.12034871199184582`, PnL `103328348417470422` wei;
- without `settlement_source`: Brier `0.2759661092894151`, Brier skill `-0.11985541074569883`, PnL `70366478896630241` wei;
- without `cex_flow`: Brier `0.2779160977226478`, Brier skill `-0.12776835738784253`, PnL `86707307639632335` wei;
- without `pool_state`: Brier `0.2793582132959623`, Brier skill `-0.13362038368141604`, PnL `80641928539824305` wei.

The full model therefore cannot be described as empirically feature-optimal from this window. Some removals improve calibration/probability loss, and some improve realized one-day PnL.

## Evidence

`evidence/recent-economic-robustness-2026-08-18-to-19-last-success.json` records:

- semantic gate `ready=true`;
- 8 exact sensitivity scenarios;
- 8 positive-PnL scenarios;
- 5 non-vacuous ablation variants over 159 common OOS epochs;
- 268 research feature rows;
- 14 candidate rounds skipped for unavailable aligned market data;
- `profitability_gate_eligible=false`;
- `full_historical_gate_satisfied=false`.

The evidence is bound to robustness implementation SHA `57e76b68709578910694a00c66137854f11c1210` and the previously proven Aug 18–19 Prediction + Chainlink source.

## Why it matters

A strategy can make money over one short sample even when its probability estimates are worse than the reference skill baseline, and feature-removal variants can outperform the full model by chance or because some features are not useful in that regime. Treating a stress-positive one-day run as profitability proof would collapse three separate claims:

1. pipeline/economic robustness;
2. predictive skill and feature value;
3. durable profitability across regimes.

Those claims require different evidence.

## Decision consequence

Keep the robustness gate as a plumbing/research gate. Do not require every scenario to be profitable for workflow success, and do not promote `all_scenarios_positive_pnl=true` into a profitability flag. Expand the source across multiple days/regimes, preserve unfavorable results, compare calibration and ablation stability, and keep the historical-source gate separate.

## Applicability

- economic sensitivity grids;
- feature ablation;
- short-window OOS smoke tests;
- model-selection decisions where realized PnL and probability skill disagree;
- automated evidence summaries that could otherwise overstate a positive backtest.

## Exceptions / Limitations

This observation does not prove the strategy lacks alpha. It only establishes that the current one-day evidence is insufficient to prove durable alpha and that the full feature set is not uniquely supported by this sample. Larger independent OOS windows and regime coverage may change the conclusion.

## Related files

- `src/pancake_prediction/campaign_sensitivity.py`
- `src/pancake_prediction/economic_ablation.py`
- `.github/workflows/recent-economic-robustness.yml`
- `config/recent-economic-sensitivity-aug18.json`
- `evidence/recent-economic-robustness-2026-08-18-to-19-last-success.json`

## Related cases

- `../cases/2026-08-20-v0-7-research-readiness/`
