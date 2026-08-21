# Candidate Rules

## Evidence-preserving CI gates must preserve gate semantics and source identity
Status: candidate
Confidence: medium

### Rule

When a GitHub Actions gate deliberately continues after a failed check so diagnostics/evidence can be persisted, preserve the original gate semantics, the identity of the source revision being validated, and the semantic meaning of success.

- If a gate step uses `continue-on-error: true`, add a later enforcement step that fails unless the original gate outcome was `success`.
- If a prerequisite is absent, fail explicitly rather than skipping every substantive step and leaving a green job.
- If the workflow uses `cancel-in-progress`, a cancelled superseded run must not overwrite current evidence with `skipped` outcomes.
- Run quality checks against the trigger SHA (or explicitly record the actual checked-out SHA), rather than silently testing a moving branch HEAD while labeling evidence with another SHA.
- Publish any artifact required by downstream evidence consumers before exposing success evidence that points to that artifact.
- Do not rely on a normal `push` made with the repository `GITHUB_TOKEN` to trigger a downstream workflow. GitHub suppresses recursive workflow runs for events created by `GITHUB_TOKEN` except explicitly supported dispatch events. Prefer an explicit reusable-workflow call or another intentionally authorized trigger.
- When a source-producing job and a downstream evaluator share one workflow run, keep the source gate identity independent from the final aggregate run conclusion. A downstream evaluation or persistence failure must not retroactively invalidate a source artifact already proven by its own gate and `last-success` evidence.
- Do not equate process exit code zero with semantic evidence success when the command can legally return an empty result. Evidence gates must assert the minimum non-vacuous conditions required by the claim being recorded.
- A prior successful evidence file may be used as a fixed semantic anchor only when its relevant identity fields, source range, and exact-byte digest are validated and every intervening semantic-change event is exhaustively scanned. Anchor use must be explicit rather than an implicit fallback.
- If computation and semantic gates succeed but repository persistence fails, recovered evidence may be created only from immutable digest-bound artifacts. Recovery must preserve the original run/job failure conclusions and explicitly identify the persistence-layer failure rather than relabeling the original job as green.

### Applicability

- repository workflows that persist diagnostics/evidence after a failing check;
- prerequisite gates such as credentials, archive readiness, external-source readiness, quality checks, or empirical research probes;
- workflows with concurrency cancellation;
- workflows where a green status or persisted JSON can be interpreted as evidence that a specific revision actually ran and passed;
- chained workflows where an upstream source artifact/evidence is consumed by a later research or evaluation stage;
- same-run reusable-workflow chains where upstream source validity and downstream evaluation validity are separate claims;
- analytical commands that can complete successfully with zero folds, zero scored rows, zero signals, zero projections, empty provenance, or another structurally valid but decision-useless result;
- fixed-route or source-identity proofs that reuse an earlier successful evidence artifact as an explicit anchor;
- evidence recovery after a storage/persistence failure when the substantive artifact already exists and is independently authenticatable.

### Verification

- identify the step that owns each gate result;
- if it uses `continue-on-error`, persist/upload evidence as needed, then explicitly enforce the original outcome;
- for prerequisite checks, include an explicit failing step when the prerequisite is absent;
- for `cancel-in-progress`, do not persist current-state evidence from cancelled runs (`always() && !cancelled()` or an equivalent guard);
- checkout the trigger SHA for revision-bound evidence, or record the actual checked-out revision if branch-head validation is intentional;
- publish required upstream artifacts before their success evidence becomes consumable;
- if an upstream workflow commit uses `GITHUB_TOKEN`, do not assume that commit will fire a downstream `push` workflow; use `workflow_call` or another explicitly supported trigger;
- bind downstream consumption to the upstream run ID, actual source SHA, event SHA, artifact identity, and source-specific success evidence;
- do not require the aggregate workflow conclusion to be `success` when a later independent downstream stage is allowed to fail while the earlier source gate remains valid;
- define semantic minimums for analytical evidence. For the recent economic pipeline smoke this means at least one research feature row, one OOS fold, one direction signal, one pool projection, one joint epoch, one scored OOS sample, and non-empty Spot/Perp provenance with the requested positive availability lags;
- for an evidence anchor, verify the exact evidence digest, identity fields, anchor range, and zero relevant change events across the full bridge from requested source start through the anchor;
- for artifact recovery, verify artifact IDs/digests and content-level source identities, then preserve original failed process conclusions plus the specific persistence failure in the recovered evidence;
- verify that normal repository CI remains green and, where practical, exercise negative/cancellation/artifact-ordering/vacuous-result paths.

### Evidence

- `.github/workflows/archive-rpc-preflight.yml` previously allowed the archive probe to fail under `continue-on-error` without a later enforcement step. Commit `394f67c6ca2f3fec33049759bdb37e3590401ae3` added explicit enforcement while preserving redacted evidence.
- `.github/workflows/historical-bootstrap.yml` previously allowed a missing `BSC_ARCHIVE_RPC_URL` to skip the substantive bootstrap steps without explicitly failing the job. Commit `5af84bbca184e6f785282bde1580591afa8a55d4` added a fail-closed prerequisite step.
- `.github/workflows/quality-evidence.yml` exposed a separate cancellation/source-identity failure: a superseded run persisted all quality outcomes as `skipped`, changed `ready` from true to false, and labeled the evidence with trigger SHA `6ad31bb4629468d45e98171b99d013970fa31c7d`. Commit `61df8e8731b92ea7f30e6011fde02cdd6d0bdd28` fixes this by checking out `${{ github.sha }}` and suppressing persistence/upload/enforcement steps on cancelled runs.
- After the fix, `evidence/quality-gate.json` recovered to `ready=true` with Ruff/mypy/pytest/Bandit/pip-audit all successful, 295 tests passed, and 87% coverage for source SHA `8adc08d8709577a10941e7bfd618a8a06215c419`.
- `.github/workflows/public-block-receipts-probe.yml`, `.github/workflows/public-archive-candidate-probe.yml`, and `.github/workflows/public-blast-bootstrap-smoke.yml` demonstrate the evidence-first-then-enforce pattern.
- The one-day Chainlink-to-economic-smoke chain exposed an artifact-ordering race: `recent-public-chainlink-day` originally pushed `last-success` evidence before uploading the SQLite artifact that the downstream smoke consumes. Commit `644eda1b84458a3361310dacac0d36f744dcd0e3` changed the order to upload the same-run artifact first, suppress cancelled-run persistence, then publish success evidence.
- The same chain exposed a second design error: success-evidence commits made by GitHub Actions with the repository `GITHUB_TOKEN` do not create ordinary recursive `push` workflow runs. Commits `af1dd5d93cf94ced71e9297296e6ef5bc0d46198` and `9a7d9f5adf564db900fd019222eefa9347853f7a` replace that implicit bot-push trigger with an explicit local reusable-workflow call. The caller passes the exact source run ID, actual collector checkout SHA, and event SHA.
- The reusable design separates upstream source validity from downstream aggregate run conclusion. One-day source run `32481332419` produced a successful source `last-success` with 14,496 Prediction events, 2,615 Chainlink updates, and 284 replay rounds even though the aggregate run later concluded failure in the independent economic persistence layer.
- `campaign-evaluate` can validly return exit code zero even when expanding-fold generation produces no folds, because an undersized feature set returns an empty fold tuple rather than raising. Commit `0af9bbd02846186147db59b315bef6e8e154c5d7` adds a separate semantic gate requiring non-empty research features, OOS folds, direction signals, pool projections, joint epochs, scored samples, and Spot/Perp provenance before the recent economic smoke can become `last-success`.
- Commit `b8e59b597ae79206d97b2a5a3faa96efe9a37ff0` separates one-day Chainlink collection success from artifact-publication success: `latest` records both outcomes while `last-success` requires both.
- The fixed Aug 18 source could not read window-end contract state from the tested public providers. `evidence/recent-chainlink-route-proof-probe-last-success.json` and `evidence/recent-public-chainlink-2026-08-18-to-19-last-success.json` instead prove an explicit SHA-bound later route anchor plus exhaustive `NewOracle` / `AggregatorConfirmed` scans through anchor block `116844485`, with both change counts zero and no historical `eth_call`.
- Economic rerun job `96810154525` empirically proved the non-vacuous pipeline after source-native timestamp-tie ordering was fixed: 268 research rows, four OOS folds, 159 signals/joint/scored epochs, 230 pool projections, and complete Spot/Perp provenance. Its final economic gate succeeded, but the GitHub job remained correctly red because a later nested-heredoc persistence block failed.
- `evidence/recent-economic-smoke-2026-08-18-to-19-last-success.json` recovers that analytical result from exact artifact ID `9451199429` / digest `sha256:f05c88d48c0087be191018e2ad55c2684b59f48ec78cb68cb9118a1695985aee`, while explicitly retaining the original job/run failure conclusions and the persistence-only failure reason. The evidence remains `profitability_gate_eligible=false` and records negative Brier skill (`-0.12435269318908326`).
- Stage 5B run `32494992355` independently demonstrates the same evidence-first pattern on a separate local-fork domain: all four observed recovery/safety scenarios, Bull/Bear finalization, zero unresolved intents, independent verification, repository persistence, and the final Stage 5B gate succeeded while signing/live broadcast remained disabled.
- Normal PR CI run `32495658341` (#985) passed with 320 tests and 87% coverage, plus Ruff, strict mypy, Bandit, pip-audit, ClickHouse integration, Gitleaks, and the pinned 144k-round audit.

### Exceptions / Limitations

This applies only when the workflow is semantically a gate or its persisted artifact represents current/revision-bound readiness. A best-effort telemetry workflow may intentionally remain green after a failed observation, and historical evidence for an older revision may be useful, but that intent and revision must be explicit. A zero-trade economic result may also be a legitimate model/economic outcome; the semantic smoke gate therefore requires a non-empty OOS evaluation path but does not require positive PnL, positive expected value, or any executed trade. Artifact recovery is not permitted if the substantive computation or semantic gate failed, and an evidence anchor is invalid if the bridge contains a relevant route-change event. This remains a Candidate Rule because broader independent reuse is still needed before promotion, even though the one-day external-source/economic chain is now empirically proven.

### Related cases / observations

- `../cases/2026-08-20-v0-7-research-readiness/`
- `../observations/chainlink-proxy-vs-aggregator.md`
- `../observations/source-native-order-breaks-timestamp-ties.md`
- `../observations/evidence-persist-failure-is-not-analytical-failure.md`

---

A Candidate Rule requires more than a single case. Promote to `validated.md` only when reproducibility, evidence, applicability, exceptions, and practical decision value are all adequate. Prefer strengthening this rule with additional independent workflow evidence over creating a duplicate.
