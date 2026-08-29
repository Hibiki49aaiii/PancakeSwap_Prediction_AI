# Issue #17 Human Understanding

## Problem

Historical Binance archive rows and prospectively fetched live rows share the same ClickHouse dedupe key.

They do not mean the same thing for information availability.

A live row records when this system actually saw the HTTP response. An archive row reconstructed later does not know that historical real-time observation moment.

If archive data is inserted later with a newer ingest version, it can replace the live row and make the trade appear available earlier than it really was during the campaign.

## Rule

Prepare archive history first.

After prospective live rows exist for a lineage, do not add archive rows to that same lineage under the current table model.

## Two protections

1. The archive ingest core checks whether live provenance already exists and refuses to write.
2. The official archive CLI uses the same lineage process lock as live writers, preventing a same-host race between the check and a live insert.

## Why not allow only older disjoint archive IDs?

That can be safe only after proving exact ID-range disjointness and preserving that invariant across every archive source and future schema change.

The current Stage 4 operating model already expects history to be prepared before the prospective campaign starts, so a conservative lineage freeze is simpler and safer.

## Future alternative

A future schema could separate historical and prospective observations by source dimension or preserve earliest/actual observation evidence in a commutative model. That is a separate migration.
