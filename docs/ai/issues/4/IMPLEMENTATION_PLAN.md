# Issue #4 — Implementation Plan

## Metadata

- Issue: #4 `feat: Stage 4 campaign Evidence checkpointを連続runtimeへ統合`
- Base Commit SHA: `9ce5beceaa9f119c9ee3c795175878ec3eff7db3`
- Target branch: `agent/issue-4-stage4-campaign-checkpoints`
- Parent development branch: `agent/v0.7-alpha-research`
- Runtime: Python >= 3.12
- Database: SQLite Shadow Ledger in WAL mode
- Scope boundary: Stage 4 prospective/no-signing operation only

## Requirements

1. Reuse one campaign Evidence payload implementation from both the standalone builder script and continuous runtime CLI.
2. Preserve existing campaign gate semantics; PnL sign must not become a pass criterion.
3. Allow runtime to checkpoint a `latest` campaign Evidence artifact after each successful cycle.
4. Allow runtime to update a `last-success` campaign Evidence artifact only when `campaign.gate_ready == true`.
5. An incomplete later cycle must not overwrite or delete an existing last-success artifact.
6. Reuse the campaign report already produced by `run_shadow_runtime_cycle()` so the Evidence audit uses the exact same `purge_rounds` and campaign policy as inference/runtime.
7. Evidence persistence must be atomic.
8. No BSC RPC URL, ClickHouse credential, private key, signing authority, funded execution, or mainnet broadcast path may be introduced or serialized.

## Current Architecture

```text
pcs-shadow-runtime
  -> run_shadow_runtime_cycle()
     -> chain sync
     -> prospective Binance sync
     -> settlement reconciliation
     -> target selection / inference
     -> append-only ShadowLedgerStore
     -> ShadowLedgerStore.audit(purge_rounds=config.inference.purge_rounds)
     -> evaluate_shadow_campaign(..., config.campaign_policy)
     -> ShadowRuntimeCycleReport(campaign=...)
  -> optional cycle JSON --evidence-output

standalone:
scripts/build_shadow_campaign_evidence.py
  -> ShadowLedgerStore.audit()
  -> evaluate_shadow_campaign()
  -> duplicated Evidence payload construction
  -> output JSON
```

The runtime already evaluates the campaign on every successful cycle. The missing layer is durable campaign-level checkpoint persistence.

## Changed Architecture

```text
                         +---------------------------+
                         | reusable Evidence builder |
                         | campaign report + DB path |
                         +-------------+-------------+
                                       |
                    +------------------+------------------+
                    |                                     |
scripts/build_shadow_campaign_evidence.py      pcs-shadow-runtime
                    |                                     |
             explicit policy/audit                 cycle.report.campaign
                    |                                     |
                    +---------------+---------------------+
                                    |
                         canonical Evidence payload
                                    |
                         atomic latest checkpoint
                                    |
                    gate_ready? ----+---- no -> preserve old last-success
                         |
                        yes
                         |
                    atomic last-success checkpoint
```

The runtime core remains free of output-path/filesystem policy. Only the CLI boundary persists Evidence.

## Data Flow

1. Runtime writes/reconciles Shadow Ledger events using existing transactions.
2. Runtime audits the ledger and evaluates the campaign with the configured purge boundary/policy.
3. Runtime returns the resulting `ShadowCampaignGateReport` in `ShadowRuntimeCycleReport`.
4. CLI builds campaign Evidence from the already-evaluated campaign report plus the Shadow DB path.
5. CLI atomically replaces latest Evidence when requested.
6. CLI atomically replaces last-success only when gate_ready is true.
7. Incomplete campaign status remains operational progress; it does not terminate continuous runtime.

## State Transition

```text
cycle failure
  -> no campaign checkpoint from that cycle
  -> existing last-success preserved

cycle success + campaign incomplete
  -> latest := incomplete Evidence
  -> last-success unchanged
  -> continue runtime

cycle success + campaign ready
  -> latest := successful Evidence
  -> last-success := successful Evidence with last_success role
  -> continue runtime
```

## SQLite / Ledger Hash Decision

`ShadowLedgerStore` uses `PRAGMA journal_mode=WAL`. The campaign's authoritative logical identity is therefore the ledger hash-chain state already contained in the audit:

- `event_count`
- `head_digest`
- `campaign_digest`

The existing standalone script also records a SHA-256 of the SQLite main database file. This field will be retained for backward compatibility, but it is treated as a physical snapshot identifier rather than the sole logical integrity proof. No forced WAL checkpoint will be added in this Issue because that would alter database/checkpoint behavior and create unnecessary coupling to concurrent readers/writers. The Evidence payload will make the logical binding explicit.

## Files Expected to Change

### Production
- `src/pancake_prediction/shadow_campaign.py`
  - reusable campaign Evidence payload builder / digest helpers, unless a narrowly-scoped sibling module is cleaner during implementation
- `src/pancake_prediction/shadow_runtime_cli.py`
  - optional latest/last-success campaign Evidence outputs
  - shared atomic JSON persistence
- `scripts/build_shadow_campaign_evidence.py`
  - delegate payload construction to reusable library

### Tests
- `tests/test_shadow_campaign.py`
- `tests/test_shadow_runtime_cli.py`
- add a dedicated Evidence test only if separation improves clarity

### Documentation
- `docs/STAGE4_SHADOW.md`
- `docs/ai/issues/4/HUMAN_UNDERSTANDING.md`

No DB schema, migration, public network protocol, or dependency change is planned.

## API / CLI Changes

New optional CLI flags:

- `--campaign-evidence-output <path>`
- `--campaign-last-success-output <path>`

Existing `--evidence-output` remains the per-cycle runtime report and keeps its current semantics.

The new flags are optional and therefore backward-compatible.

## Error Handling

- Source integrity, ledger integrity, malformed Evidence state, and filesystem write failures remain visible failures.
- Campaign gate incomplete is not an exception and does not stop the runtime.
- A last-success path is never touched on incomplete campaigns.
- Temporary files are replaced atomically.
- No exception message may interpolate configured secret endpoint/password values.

## Security Considerations

- No signer/key/wallet code is added.
- No transaction method is added.
- Evidence keeps:
  - `signing_enabled=false`
  - `live_broadcast=false`
  - `funded_execution=false` where applicable
  - `profitability_gate_eligible=false`
  - `full_historical_gate_satisfied=false`
- RPC/ClickHouse configuration remains environment-only and absent from serialized Evidence.

## Testing Strategy

### Targeted
- campaign Evidence payload preserves gate and safety semantics
- negative-PnL ready campaign remains ready
- runtime incomplete cycle writes latest but not last-success
- pre-existing last-success bytes remain unchanged after incomplete cycle
- ready campaign writes both files with correct roles
- runtime config purge policy is represented by the already-evaluated campaign report
- cycle/campaign evidence contains no endpoint/password secrets
- atomic temp files do not remain after successful replacement

### Full
- `ruff check .`
- `mypy src tests`
- `pytest --cov=src/pancake_prediction --cov-report=term-missing`
- `bandit -c pyproject.toml -r src`
- `pip-audit`
- GitHub Actions CI: quality, ClickHouse integration, legacy round audit, Gitleaks

## Implementation Order

1. Extract/reuse campaign Evidence payload construction.
2. Refactor standalone script without semantic changes.
3. Add runtime CLI checkpoint flags and helper.
4. Add incomplete/last-success preservation tests.
5. Add ready checkpoint tests and secret non-disclosure coverage.
6. Update Stage 4 documentation and human summary.
7. Run targeted tests.
8. Run full local verification.
9. Push and inspect GitHub Actions.
10. Post-implementation correctness/regression/security/maintainability/dead-code review.
11. Update Issue #4 actual files/results/checklists.

## Rollback

All changes are additive/refactor-only and can be reverted without DB migration. Existing runtime invocation remains valid when new flags are absent.

## Known Risks

- SQLite physical DB-file SHA is not by itself a complete logical proof in WAL mode; authoritative logical binding remains event-chain head/count + campaign digest.
- External concurrent writers are outside the intended single-runtime campaign model.
- Filesystem exhaustion/permission errors can prevent Evidence persistence and must not be reported as success.
- A future campaign-policy CLI surface could create drift if it is not bound to runtime configuration; this Issue deliberately does not add that second configuration path.

# Pre-Implementation Review

## Pass 1 — Requirements

Finding: runtime already computes `ShadowCampaignGateReport` using the same purge boundary as inference. Re-auditing in the CLI would add a second possible source of specification drift.

Triage: **Adopt.** The runtime checkpoint path will consume `report.campaign` directly. The standalone script will still perform its own audit because it has no runtime report.

Finding: a successful runtime cycle with an incomplete campaign is valid progress.

Triage: **Adopt.** Incomplete is represented in latest Evidence but is never converted into a runtime failure.

## Pass 2 — Architecture

Finding: placing output paths inside `run_shadow_runtime_cycle()` would mix domain execution and persistence policy.

Triage: **Reject.** Keep checkpoint persistence in the CLI.

Finding: shelling out to the existing script would duplicate policy arguments and reduce type/test coverage.

Triage: **Reject.** Use a reusable Python library function.

Finding: the existing atomic JSON writer can be generalized instead of creating a second writer.

Triage: **Adopt.**

## Pass 3 — Risk

Finding: physical SQLite file SHA is potentially weaker than logical ledger identity in WAL mode.

Triage: **Adopt as documented limitation.** Keep compatibility field, make hash-chain state/campaign digest explicit, do not introduce forced WAL checkpoint behavior.

Finding: latest-success overwrite on an incomplete cycle would destroy established evidence.

Triage: **Adopt.** Add explicit preservation regression test.

Finding: output paths themselves are not secrets, but endpoint/password values are.

Triage: **Adopt.** Extend existing no-secret CLI test to campaign files.

Finding: changing default campaign thresholds is unrelated.

Triage: **Out of scope.**

## Review Conclusion

The design satisfies Issue #4 without changing trading semantics, source acquisition, model logic, database schema, or execution authority. Implementation may proceed.

# Integration Refresh — 2026-08-29

Issue #4 の初回実装検証後、親 `agent/v0.7-alpha-research` には以下の Stage 4 runtime 改善が追加された。

- Issue #7: target-bounded ClickHouse research dataset
- Issue #8: single-target pool projection
- Issue #9: monotonic phase-latency Evidence

PR #5 を旧baseのまま残さず、最新親 SHA `6de451b12293083103ebe5fc38a4112cbeab06b8` から再構成し、Issue #4 の10ファイル差分だけを再適用した。

競合解決方針:

- production/test filesはIssue #7〜#9で変更されていなかったためIssue #4差分をそのまま再適用;
- `.ai/index.md` はruntime optimization observationを保持し、SQLite WAL observationを追加;
- `docs/STAGE4_SHADOW.md` はIssue #7〜#9のbounded dataset / single-target projection / latency Evidence節を保持し、campaign checkpoint節だけを追加.

Rebased PR #5 head `ba7eb86076a4c9bb3a9f02946794d08639254f32` は CI #1215 / run `33240396419` で再検証済み:

- Ruff: success
- mypy strict: success
- pytest: **409 passed in 25.30s**
- coverage: **87%**
- Bandit: success
- pip-audit: success
- Gitleaks: success
- ClickHouse integration: success
- pinned legacy audit: **144,000 rows success**
- overall CI: **success**

この再統合でも signer / mainnet broadcast / funded execution / profitability promotion は追加していない。

