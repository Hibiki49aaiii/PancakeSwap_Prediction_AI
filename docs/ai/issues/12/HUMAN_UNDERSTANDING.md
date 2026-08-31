# Issue #12 Human Understanding

## Problem

A long-running Shadow Ledger must represent one reproducible campaign.

Today, changing some runtime settings across a restart can produce predictions under different economics/source timing while still sharing one ledger.

## Solution

The first Stage 4 runtime cycle binds an immutable, deterministic campaign manifest to an empty Shadow Ledger.

Later cycles must present the exact same semantic manifest. A mismatch stops before reconciliation or prediction append.

## Important distinction

The manifest identifies **decision semantics**, not every performance tuning knob.

Changing ClickHouse chunk sizes or HTTP batch sizes should not create a new campaign if they do not change the information used at decision time.

Changing stake, gas, latency, source lineage, oracle anchors, model/training settings or campaign policy does create a different campaign identity.

## Existing ledgers

An old ledger that already has prediction/settlement events but no manifest cannot be safely assigned a manifest after the fact, so the runtime rejects it rather than guessing.

## Safety

The manifest contains no RPC URL, ClickHouse credentials, private key, signer or broadcast authority.
