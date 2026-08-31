# Validated Rules

## Profitability Evidence Boundary
Status: validated
Confidence: high

### Rule

When evaluating claims that the PancakeSwap Prediction system is profitable or ready to trade, do not treat green CI, parser success, data ingestion success, schema consistency, model accuracy, or a single positive backtest as profitability evidence. Require source-bound out-of-sample economic evaluation under explicit realistic costs/latency, and reject results that are unstable across relevant sensitivity/ablation/regime checks.

### Applicability

- research/economic campaign evaluation;
- model or feature-family promotion;
- claims of alpha, profitability, or readiness to move beyond research/shadow gates.

### Verification

- bind the campaign to canonical history, oracle state, exact source slices, and timing assumptions;
- use purged/embargoed OOS fitting/evaluation rather than in-sample performance;
- supply explicit stake, bet gas, claim gas, and inclusion latency where the evaluator requires them;
- include payout dilution/post-decision pool behavior and other configured economics;
- compare feature-family ablations and sensitivity/regime behavior;
- keep software/data-contract validation claims separate from economic claims.

### Evidence

- `README.md` states that prediction accuracy alone is not a profitability criterion.
- PR #1 defines source-bound, purged/embargoed, cost-aware evaluation and explicitly rejects one positive backtest as profitability evidence.
- `evidence/binance-real-sample-2026-08-01.json` explicitly states that its zero availability lag is parser/ingest validation only and is not profitability evidence.
- `docs/CAMPAIGN_EVALUATION.md` and current campaign tests implement the economic/OOS boundary.

### Exceptions / Limitations

Green CI and ingestion evidence remain valid evidence for the narrower properties they actually test. This rule prevents upgrading those properties into an economic claim; it does not diminish their software/data-quality value.

### Related cases / observations

- `../cases/2026-08-20-v0-7-research-readiness/`

---

Add a new validated rule only after the promotion criteria in `../README.md` are satisfied. Prefer updating an existing rule with stronger evidence over duplicating it.
