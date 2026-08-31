# Repository Decision Invariants

Status: active
Date: 2026-08-20

These are high-value decision constraints, not a substitute for reading current code/tests.

- **Canonical scope:** v0.7+ work belongs in this repository; legacy prediction artifacts are reference material, not the implementation source of truth.
- **Information availability:** a feature is admissible only if the information was actually available before the configured decision cutoff. Source timestamp alone is insufficient when arrival/processing latency differs.
- **No final-pool leakage:** final Bull/Bear pool values are not decision features. Pool evolution must be projected from information available at the decision point.
- **Economic objective:** prediction accuracy alone is not a profitability criterion. Evaluation must bind stake, participation/treasury fee effects, bet/claim gas, own-bet dilution, post-decision pool movement, inclusion latency, and execution uncertainty as applicable.
- **Source provenance:** research claims should be tied to the exact canonical history/oracle/data slices and timing assumptions used by the campaign, not merely to a broad dataset name.
- **Historical oracle correctness:** historical Chainlink-derived features must use the oracle active at the exact canonical EVM position being evaluated.
- **Fail-closed validation:** training/calibration/pool-projection boundaries and schema/source assumptions should fail closed rather than silently accepting ambiguous or incompatible data.
- **Research/execution separation:** research/model code has no signing authority. The transaction-capable Stage 5 path remains loopback/local-fork only unless a separately authorized future stage deliberately changes that boundary.
- **Evidence boundary:** green software/data-contract checks establish implementation properties; they do not establish trading profitability.

## Revalidate against

- `README.md`
- `docs/ALPHA_RESEARCH_V0_7.md`
- `docs/CAMPAIGN_EVALUATION.md`
- `docs/STAGE5_FORK_EXECUTION.md`
- current tests and persisted `evidence/` artifacts
- active Decisions and Validated Rules under `.ai/`
