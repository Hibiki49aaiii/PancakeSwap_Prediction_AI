# Candidate Rules

## Evidence-preserving CI gates must preserve gate semantics and source identity
Status: candidate
Confidence: medium

### Rule

When a GitHub Actions gate deliberately continues after a failed check so diagnostics/evidence can be persisted, preserve the original gate semantics and the identity of the source revision being validated.

- If a gate step uses `continue-on-error: true`, add a later enforcement step that fails unless the original gate outcome was `success`.
- If a prerequisite is absent, fail explicitly rather than skipping every substantive step and leaving a green job.
- If the workflow uses `cancel-in-progress`, a cancelled superseded run must not overwrite current evidence with `skipped` outcomes.
- Run quality checks against the trigger SHA (or explicitly record the actual checked-out SHA), rather than silently testing a moving branch HEAD while labeling evidence with another SHA.

### Applicability

- repository workflows that persist diagnostics/evidence after a failing check;
- prerequisite gates such as credentials, archive readiness, external-source readiness, quality checks, or empirical research probes;
- workflows with concurrency cancellation;
- workflows where a green status or persisted JSON can be interpreted as evidence that a specific revision actually ran and passed.

### Verification

- identify the step that owns the gate result;
- if it uses `continue-on-error`, persist/upload evidence as needed, then explicitly enforce the original outcome;
- for prerequisite checks, include an explicit failing step when the prerequisite is absent;
- for `cancel-in-progress`, do not persist current-state evidence from cancelled runs (`always() && !cancelled()` or an equivalent guard);
- checkout the trigger SHA for revision-bound evidence, or record the checked-out revision if branch-head validation is intentional;
- verify that normal repository CI remains green and, where practical, exercise the negative/cancellation path.

### Evidence

- `.github/workflows/archive-rpc-preflight.yml` previously allowed the archive probe to fail under `continue-on-error` without a later enforcement step. Commit `394f67c6ca2f3fec33049759bdb37e3590401ae3` added explicit enforcement while preserving redacted evidence.
- `.github/workflows/historical-bootstrap.yml` previously allowed a missing `BSC_ARCHIVE_RPC_URL` to skip the substantive bootstrap steps without explicitly failing the job. Commit `5af84bbca184e6f785282bde1580591afa8a55d4` added a fail-closed prerequisite step.
- `.github/workflows/quality-evidence.yml` exposed a separate cancellation/source-identity failure: a superseded run persisted all quality outcomes as `skipped`, changed `ready` from true to false, and labeled the evidence with trigger SHA `6ad31bb4629468d45e98171b99d013970fa31c7d`. Commit `61df8e8731b92ea7f30e6011fde02cdd6d0bdd28` fixes this by checking out `${{ github.sha }}` and suppressing persistence/upload/enforcement steps on cancelled runs.
- After the fix, `evidence/quality-gate.json` recovered to `ready=true` with Ruff/mypy/pytest/Bandit/pip-audit all successful, 295 tests passed, and 87% coverage for source SHA `8adc08d8709577a10941e7bfd618a8a06215c419`.
- `.github/workflows/public-block-receipts-probe.yml`, `.github/workflows/public-archive-candidate-probe.yml`, and `.github/workflows/public-blast-bootstrap-smoke.yml` demonstrate the evidence-first-then-enforce pattern.
- PR CI run 797 passed after the earlier fail-closed workflow changes, and later quality evidence confirms the recent bootstrap changes are also green.

### Exceptions / Limitations

This applies only when the workflow is semantically a gate or its persisted artifact represents current/revision-bound readiness. A best-effort telemetry workflow may intentionally remain green after a failed observation, and historical evidence for an older revision may be useful, but that intent and revision must be explicit. Although three independent workflow failure modes now support this rule, the repository has not yet exercised every negative/cancellation path under dedicated tests, so it remains a Candidate Rule rather than a Validated Rule.

### Related cases / observations

- `../cases/2026-08-20-v0-7-research-readiness/`

---

A Candidate Rule requires more than a single case. Promote to `validated.md` only when reproducibility, evidence, applicability, exceptions, and practical decision value are all adequate. Prefer strengthening this rule with additional independent workflow evidence over creating a duplicate.
