# Campaign Evidence must bind semantic identity across restarts

Status: observation / high
Date: 2026-08-29

A long-running Shadow campaign is not defined only by its model ID, feature-set ID, or ledger file path.

## Rule

Campaign Evidence must bind an immutable semantic identity across restarts.

That identity should include every setting that can materially change the decision/economic contract, including:

- market and Prediction contract identity;
- oracle proxy and Chainlink aggregator anchors;
- source timing lineage;
- feature timing and availability assumptions;
- training/calibration/purge/pool-projection semantics;
- stake, gas, latency, and minimum-EV assumptions;
- campaign evaluation policy.

A runtime restart must fail closed if the new semantic manifest differs from the manifest already bound to an event-bearing ledger.

## Do not retroactively adopt ambiguous history

If a ledger already contains prediction or settlement events but has no campaign manifest, do not infer a manifest from the current configuration and attach it after the fact.

The historical decisions may have been produced under different semantics. Automatic adoption would convert an unknown provenance state into an apparently proven campaign identity.

Start a fresh manifest-bound campaign instead.

## Audit configuration is part of campaign interpretation

Binding the runtime manifest is not enough if a later audit is allowed to reinterpret the same events with a different purge boundary or campaign policy.

The ledger audit and campaign gate must verify that:

- audit purge semantics match the bound inference manifest;
- the evaluation policy matches the policy bound in the manifest;
- prediction market identity matches the bound manifest.

## Performance tuning is different

Operational settings that do not change decision semantics should not unnecessarily fragment campaign identity. Examples include:

- ClickHouse query chunk size;
- HTTP page/batch size;
- historical log chunk size;
- bootstrap query window.

By contrast, the chain reorg lookback is part of source-integrity semantics: changing how far the runtime re-proves/reconciles canonical chain state can change which source history is accepted, so it belongs in campaign identity.

Pure performance settings may change runtime cost or latency, but not the semantic definition of one prediction.

## Evidence binding

Logical Stage 4 Evidence should bind at least:

- campaign manifest digest;
- append-only event count;
- hash-chain head digest;
- campaign digest.

A physical SQLite main-file SHA remains only a snapshot identifier, especially under WAL mode.

## Revalidate against

- `src/pancake_prediction/shadow_manifest.py`
- `src/pancake_prediction/shadow_ledger.py`
- `src/pancake_prediction/shadow_runtime.py`
- `src/pancake_prediction/shadow_campaign.py`
- Issue #12 tests
