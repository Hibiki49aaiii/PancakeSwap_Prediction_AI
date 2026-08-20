# Candidate Rules

## Evidence-preserving CI gates must re-enforce failure
Status: candidate
Confidence: medium

### Rule

When a GitHub Actions gate step uses `continue-on-error: true` so that evidence can still be persisted or uploaded after a failed probe/check, add a later unconditional enforcement step that fails the job unless the original gate outcome was `success`. Likewise, a missing prerequisite must fail the gate explicitly rather than causing every substantive step to be skipped while the job remains successful.

### Applicability

- repository workflows that persist diagnostics/evidence after a failing check;
- prerequisite gates such as credentials, archive readiness, external-source readiness, quality checks, or empirical research probes;
- workflows where a green status could be interpreted as evidence that an operation actually ran and passed.

### Verification

- identify the step that owns the gate result;
- if it uses `continue-on-error`, persist/upload evidence with `if: always()` as needed;
- after evidence handling, check `${{ steps.<gate>.outcome }}` and exit non-zero unless it is `success`;
- for prerequisite checks, include an explicit failing step when the prerequisite is absent;
- verify that the ordinary repository CI remains green and, where practical, exercise the negative gate path.

### Evidence

- `.github/workflows/archive-rpc-preflight.yml` previously allowed the archive probe to fail under `continue-on-error` without a later enforcement step. Commit `394f67c6ca2f3fec33049759bdb37e3590401ae3` added explicit enforcement while preserving redacted evidence.
- `.github/workflows/historical-bootstrap.yml` previously allowed a missing `BSC_ARCHIVE_RPC_URL` to skip the substantive bootstrap steps without explicitly failing the job. Commit `5af84bbca184e6f785282bde1580591afa8a55d4` added a fail-closed prerequisite step.
- `.github/workflows/quality-evidence.yml`, `.github/workflows/public-block-receipts-probe.yml`, `.github/workflows/public-archive-candidate-probe.yml`, and `.github/workflows/public-blast-bootstrap-smoke.yml` demonstrate the intended evidence-first-then-enforce structure.
- PR CI run 797 passed after the fail-closed workflow changes, showing no regression in the normal repository quality suite.

### Exceptions / Limitations

This applies only when the workflow is semantically a gate. A best-effort telemetry or optional diagnostic workflow may intentionally remain green after a failed observation, but that intent must be explicit and its status must not be used as readiness evidence. Runtime validation of every negative path has not yet been established, so this remains a Candidate Rule rather than a Validated Rule.

### Related cases / observations

- `../cases/2026-08-20-v0-7-research-readiness/`

---

A Candidate Rule requires more than a single case. Promote to `validated.md` only when reproducibility, evidence, applicability, exceptions, and practical decision value are all adequate. Prefer strengthening this rule with additional independent workflow evidence over creating a duplicate.
