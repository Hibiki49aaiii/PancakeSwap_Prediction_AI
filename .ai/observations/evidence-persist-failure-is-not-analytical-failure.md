# Evidence Persistence Failure Is Not Analytical Failure
Status: observation
Date: 2026-08-22
Source case: `../cases/2026-08-20-v0-7-research-readiness/`
Confidence: high

## Context

The successful Aug 18 one-day economic rerun completed exact-source resolution, source artifact validation, ClickHouse ingest, campaign evaluation, non-vacuous semantic validation, artifact upload, and the final economic gate. The GitHub job nevertheless concluded `failure` because the subsequent repository-evidence persistence shell block contained a nested-heredoc syntax error.

## Observation

Workflow process conclusion, analytical result, artifact publication, and repository evidence persistence are distinct claims. A persistence-layer failure must not be rewritten as analytical success, but it also must not erase already-proven analytical outcomes.

When recovering evidence after a persistence-only failure:

1. bind the recovery to exact artifact IDs and artifact digests;
2. validate the artifact contents and source identity independently;
3. retain the original workflow run ID, attempt, job ID, and their actual failure conclusions;
4. record which substantive analytical and semantic gates succeeded;
5. record the persistence failure and its exact reason;
6. mark the evidence as recovered from immutable artifacts rather than pretending the original job was green;
7. keep interpretation boundaries such as `profitability_gate_eligible=false` intact.

## Evidence

Economic rerun job `96810154525` in parent run `32481332419`, attempt 2:

- source resolution: success;
- exact one-day source artifact: success;
- ClickHouse schema: success;
- Binance Spot/Perp download and ingest: success;
- campaign evaluation: success;
- non-vacuous semantic validation: success;
- final economic-result enforcement: success;
- artifact upload: success;
- repository persist step: failure due nested shell heredoc syntax;
- parent run and economic job therefore correctly remain recorded as `failure`.

Recovery is bound to:

- economic artifact ID `9451199429`, digest `sha256:f05c88d48c0087be191018e2ad55c2684b59f48ec78cb68cb9118a1695985aee`;
- source artifact ID `9447571663`, digest `sha256:a4412af5a8247075e2b2f44d5cfa1067d391de59314a9c180eaa5b720573a0f1`;
- source run `32481332419`;
- source collector SHA `2cb5dcb374880b20e4b6f859991bea92dce6ba95`;
- source event SHA `a831220e32173a78e879df4024e60f4ffcba6e19`;
- economic checkout SHA `05d5247b65656f7f1d293f15e733329af9f0a53b`.

`evidence/recent-economic-smoke-2026-08-18-to-19-last-success.json` records both the successful analytical semantics and the original persistence failure provenance. The canonical workflow persistence block was subsequently repaired to avoid nested heredocs.

## Why it matters

Collapsing these layers into one boolean can create two opposite errors: discarding valid analytical evidence because Git persistence failed, or falsely reporting a failed GitHub job as green. Explicit layered provenance preserves both facts.

## Applicability

- evidence-producing CI workflows;
- external-data experiments where artifacts are immutable and independently digest-bound;
- `continue-on-error` workflows with later semantic enforcement;
- recovery after storage, upload, or repository-persistence failures.

## Exceptions / Limitations

Artifact recovery is valid only when the substantive outputs already existed before the persistence failure and can be independently authenticated. It must not be used to reconstruct missing computations, bypass failed semantic gates, or convert a genuinely failed analytical run into success.

## Related files

- `.github/workflows/recent-economic-smoke.yml`
- `evidence/recent-economic-smoke-2026-08-18-to-19-latest.json`
- `evidence/recent-economic-smoke-2026-08-18-to-19-last-success.json`

## Related cases

- `../cases/2026-08-20-v0-7-research-readiness/`
