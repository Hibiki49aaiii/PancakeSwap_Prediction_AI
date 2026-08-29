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
- chain confirmations;
- Spot/Perp lineage identity;
- flow/age/Chainlink feature timing semantics;
- full `ShadowInferenceConfig`;
- full `ShadowCampaignPolicy`.

Performance-only settings deliberately excluded from the digest:

- chain log chunk size;
- chain reorg scan window;
- Binance HTTP batch/page limits;
- ClickHouse dataset chunk span;
- Binance bootstrap window.

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
