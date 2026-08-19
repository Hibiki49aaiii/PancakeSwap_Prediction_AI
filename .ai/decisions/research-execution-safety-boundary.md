# Research and Execution Safety Boundary
Status: active
Date: 2026-08-20

## Context

The repository combines historical/model/economic research with Stage 5 execution-readiness engineering. Mixing signing authority into research code would enlarge the blast radius and make research automation capable of unintended funded actions.

## Decision

Research and model layers do not hold private keys or signing authority. The transaction-capable Stage 5 adapter remains hard loopback/local-fork only, with `live_broadcast=false` and `signing_enabled=false` in the current v0.7 scope. AI/LLM components may assist research/evaluation/explanation but are not wallet controllers.

## Alternatives considered

- wire a live signer into the research CLI for convenience;
- allow non-loopback transaction endpoints while keeping signing disabled;
- keep transaction semantics isolated behind the local-fork Stage 5 gate.

The third option is the active decision.

## Evidence / Rationale

- `README.md` explicitly separates research/model code from signing authority and limits the Stage 5 transaction adapter to loopback local-fork endpoints.
- PR #1 states that no private key, mnemonic, signer, wallet unlock, raw transaction, or mainnet broadcast path is introduced and that the transaction-capable adapter remains loopback/local-fork only.
- `docs/STAGE5_FORK_EXECUTION.md` defines the fork execution safety contract.
- Tests cover execution intent recovery/reconciliation and local-fork RPC behavior; recent history includes dedicated Anvil reorg injection coverage.

## Tradeoffs

This slows any future transition to funded validation because a separate, explicit safety design/gate will be required. The benefit is a materially smaller research blast radius and clearer evidence about what has and has not been authorized.

## Consequences

Do not solve research or test friction by introducing secrets/signers/mainnet broadcast into the current path. Any future funded stage must be separately authorized, designed, reviewed, and verified rather than emerging as an incidental extension of v0.7 tooling.

## Revisit when

Only when the user explicitly authorizes a later funded-validation stage and the repository has satisfied the preceding historical, OOS economic, shadow, and local-fork evidence gates.

## Related code

- `src/pancake_prediction/execution_intent.py`
- `src/pancake_prediction/prediction_tx.py`
- `src/pancake_prediction/rpc.py`
- `src/pancake_prediction/stage5_evidence.py`
- `docs/STAGE5_FORK_EXECUTION.md`
