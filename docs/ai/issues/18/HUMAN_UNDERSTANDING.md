# Issue #18 Human Understanding

## Problem

A Stage 4 campaign can have valid historical Binance rows before live collection begins. That is normal.

After prospective live collection starts, however, the latest lineage row must continue to come from the live REST source. If a later archive/non-live source advances the lineage, information availability may no longer represent what the runtime actually knew in real time.

The normal runtime already detects this, but preflight did not.

## Correct preflight behavior

### Historical bootstrap

If no live REST rows exist yet, archive-only history is allowed.

### Prospective lineage

If live REST rows exist, preflight verifies that the latest cursor is also from that live REST source.

If not, campaign-start readiness is false before normal runtime begins.

## Why reuse runtime helpers

The same coverage and cursor helpers are used by runtime and preflight so the two entrypoints cannot silently drift into different definitions of source integrity.

## Scope

This detects current logical lineage inconsistency. It does not repair ClickHouse state or recover live rows already physically eliminated by prior merges.
