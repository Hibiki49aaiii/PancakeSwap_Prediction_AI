# Issue #12 Implementation Plan

## Goal

Bind every Stage 4 Shadow campaign to one immutable semantic manifest so a restarted runtime cannot silently mix materially different source/model/economic semantics into the same Shadow Ledger.

## Existing gap

The current campaign gate limits model IDs and feature-set IDs, but those identifiers do not cover all runtime semantics. Costs, latency, source lineage, oracle anchors and policy can change without necessarily changing the model ID.

## Architecture

### 1. Canonical manifest value object

Add `shadow_manifest.py` containing a frozen `ShadowCampaignManifest`.

The manifest contains only controlled, non-secret semantic fields and exposes:

- canonical JSON payload;
- deterministic SHA-256 digest.

### 2. Shared runtime manifest builder

Add a shared builder next to `ShadowRuntimeConfig` so normal runtime and read-only preflight use the exact same semantic projection.

Semantic identity includes:

- BSC chain id, market, Prediction contract;
- oracle proxy + Chainlink aggregator anchors;
- chain confirmations and reorg lookback;
- Spot/Perp lineage identity;
- flow/age/Chainlink feature timing semantics;
- full `ShadowInferenceConfig`;
- full `ShadowCampaignPolicy`.

Performance-only settings deliberately excluded from the digest:

- chain log chunk size;
- Binance HTTP batch/page limits;
- ClickHouse dataset chunk span;
- Binance bootstrap window.

The chain reorg lookback is included in semantic identity because it changes the source-integrity/reconciliation boundary.

Those settings may change runtime cost/throughput but must not force a new semantic campaign when the decision contract is unchanged.

### 3. Immutable ledger binding

Extend `ShadowLedgerStore.initialize()` with additive tables/triggers only.

Create a singleton manifest table with:

- canonical manifest JSON;
- manifest SHA-256.

UPDATE and DELETE are rejected.

`bind_campaign_manifest()` semantics:

- empty ledger + no manifest: insert;
- existing identical manifest: idempotent success;
- existing different manifest: fail closed;
- non-empty event ledger + no manifest: reject retroactive adoption.

No existing hash-chain event is rewritten.

### 4. Audit and campaign Evidence

Ledger audit reports manifest digest.

Campaign gate requires a bound manifest.

Campaign digest includes the manifest digest.

Campaign Evidence requires and serializes the exact manifest digest alongside event count/head/campaign digest.

### 5. Runtime ordering

Normal cycle:

1. schema validation;
2. chain sync / route proof;
3. Binance sync and coverage;
4. canonical input load;
5. Shadow Ledger initialize;
6. **manifest construct + bind/verify**;
7. settlement reconciliation;
8. warmup / target / inference / append;
9. campaign audit/checkpoint.

Thus route drift still fails in the existing chain-sync proof before manifest acceptance, while no settlement/prediction can enter a mismatched campaign.

### 6. Preflight

Preflight never initializes or binds the Shadow Ledger.

When stored anchors are valid, it constructs the same expected manifest and reports canonical payload + digest.

## Backward compatibility

Old event-bearing ledgers without a manifest are intentionally not auto-adopted by the Stage 4 runtime. Their semantic identity cannot be proven retrospectively.

Empty ledgers created by an older schema can be upgraded and bound safely.

## Safety

No secret, endpoint URL, username/password, signer, wallet or broadcast capability enters the manifest.

## Tests

- manifest determinism;
- semantic field drift changes digest;
- performance-only changes preserve digest;
- first bind / idempotent retry / conflict;
- non-empty unbound ledger rejected;
- UPDATE/DELETE triggers;
- audit and campaign Evidence binding;
- runtime binds before reconciliation;
- restart drift fails closed;
- preflight expected digest equals runtime builder digest;
- no-secret serialization.

## Verification

- Ruff
- mypy strict
- pytest + coverage
- Bandit
- pip-audit
- Gitleaks
- ClickHouse integration
- pinned 144,000-round audit

## Base

- branch: `agent/v0.7-alpha-research`
- base SHA: `078622bde009d26567202e93e5db632b651f5702`
- 418 tests / 87% / CI #1233 green.


# Implementation Result — 2026-08-29

Issue #12 implementation is complete.

## Implemented

- Added `src/pancake_prediction/shadow_manifest.py` with deterministic canonical JSON and SHA-256 campaign identity.
- Added the shared `build_shadow_runtime_campaign_manifest()` builder used by both normal runtime and read-only preflight.
- Semantic identity binds:
  - BSC chain id, market and Prediction contract;
  - oracle proxy and Chainlink aggregator anchors;
  - chain confirmations and reorg lookback;
  - configured Binance Spot lineage and optional USD-M Perp lineage;
  - feature freshness / Chainlink hazard semantics;
  - complete `ShadowInferenceConfig`;
  - complete `ShadowCampaignPolicy`.
- Pure throughput/query tuning remains outside the digest:
  - chain log chunk size;
  - Binance bootstrap/page/batch limits;
  - ClickHouse dataset chunk span.
- Added immutable singleton `shadow_campaign_manifest` storage to the Shadow Ledger.
- Manifest UPDATE/DELETE is blocked by SQLite triggers.
- Empty-ledger first bind is transactional.
- Exact restart retry is idempotent.
- Conflicting semantic manifest fails closed.
- Event-bearing unbound ledgers cannot be retroactively adopted.
- Runtime ordering is now route proof / canonical load / ledger init / manifest bind / reconciliation.
- Runtime cycle Evidence exposes the bound campaign manifest digest.
- Preflight constructs and reports the exact expected manifest/digest without writing or binding the Shadow Ledger.
- Ledger audit reports and validates:
  - bound manifest digest;
  - audit purge boundary;
  - manifest market;
  - manifest campaign-policy digest.
- Audit fails integrity when `purge_rounds` differs from the bound inference manifest.
- Audit fails integrity when prediction market differs from the bound manifest.
- Campaign gate requires:
  - a bound manifest;
  - campaign policy digest equal to the policy bound in the manifest.
- Campaign digest includes the manifest digest.
- Campaign Evidence binds manifest digest + event count + hash-chain head + campaign digest.
- Manifest canonicalization rejects secret-bearing keys such as credential/password/private-key/token/URL fields.
- Added reusable External Intelligence:
  - `.ai/observations/campaign-evidence-must-bind-semantic-identity-across-restarts.md`
  - indexed in `.ai/index.md`.

## Design correction from Post-Implementation Review

The initial plan classified the chain reorg scan window as performance-only. Review showed that this was incorrect: `chain_reorg_lookback` changes how far canonical chain history is rechecked after restart/reorg and therefore changes accepted source-integrity semantics.

The final implementation binds `chain_reorg_lookback` into the campaign manifest. Only pure chunk/page/batch/query sizing remains outside semantic identity.

## Compatibility

- Empty legacy Shadow Ledger schemas upgrade additively and can bind a manifest.
- Existing event-bearing unbound ledgers remain inspectable with low-level ledger tooling but cannot pass the Stage 4 campaign gate and cannot be auto-adopted by the runtime.
- Existing event hash-chain rows are not rewritten.

## Verification

Final production/test source SHA:
`360a893f7428f8f37e70454bc056f9496316e8bb`

Quality Evidence #274 / run `33242532454`:

- pytest: **441 passed**
- coverage: **87%**
- Ruff: success
- mypy strict: success
- Bandit: success
- pip-audit: success
- final quality gate: success

Full PR CI #1262 / run `33242534240` on the same source SHA:

- pytest: **441 passed in 23.59s**
- coverage: **87%**
- test: success
- ClickHouse integration: success
- Gitleaks: success
- pinned legacy **144,000-round** audit: success
- overall CI: success

Current bot writeback head `9cd4a6657d4a0e89f2e398ab9fde1dd3a0500623` changes only `evidence/quality-gate.json` and binds the green result to source SHA `360a893f...`.

## Post-Implementation Review

### Correctness

Restarted runtime instances cannot silently mix materially different source, inference, economic, purge, or campaign-policy semantics into one manifest-proven campaign.

### Architecture

The immutable manifest is separate from the append-only event hash chain. This avoids rewriting historical events while still making campaign identity independently auditable.

### Evidence

Stage 4 Evidence now requires both semantic identity and logical ledger identity. A physical SQLite file SHA remains only a snapshot identifier.

### Security

No RPC URL, ClickHouse credential, secret, signer, private key, transaction signing, mainnet broadcast, or funded execution capability was introduced.

### Remaining external blockers

Issue #12 does not solve the authenticated historical BSC RPC blocker, does not prove the default long-running Stage 4 campaign empirically, and does not promote profitability/full-history/funded-execution gates.
