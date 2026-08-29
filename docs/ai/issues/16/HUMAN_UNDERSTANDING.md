# Issue #16 Human Understanding

## Problem

The Shadow runtime lock prevents two processes from using the same Shadow Ledger, but two different campaigns can still share one ClickHouse Binance live lineage.

Both may read the same latest trade ID and fetch the same next trades.

Because the stored availability time is based on the actual HTTP response observation time, those duplicate rows are not semantically identical.

## Solution

Official live writers coordinate on a second lock whose identity is the ClickHouse Binance lineage itself rather than the Shadow campaign.

The same lock is used by:

- continuous/once Stage 4 runtime;
- manual `pcs-clickhouse binance-live-sync`.

## What defines one lineage lock

- ClickHouse endpoint + database;
- market;
- venue;
- timestamp unit;
- availability lag.

Spot and Perp therefore have separate locks.

## Privacy

The lock filename contains only a SHA-256 identity digest. It does not expose the endpoint, username, password or credential.

## Scope

This is a local-host process lock. A distributed deployment using multiple hosts would require a later distributed coordination design or a data-model change that makes duplicate prospective observations commutative.
