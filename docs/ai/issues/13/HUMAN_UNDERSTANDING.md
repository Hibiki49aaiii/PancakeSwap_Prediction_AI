# Issue #13 Human Understanding

## Problem

The Stage 4 preflight can currently say the external sources and expected campaign manifest are ready without checking whether the selected existing Shadow Ledger belongs to that same campaign.

The normal runtime is still safe because Issue #12 rejects manifest drift before reconciliation, but the rejection happens after source synchronization has already started.

## Desired behavior

Preflight should answer one more question:

> If I start the normal runtime with this exact configuration and this exact Shadow Ledger, is the ledger manifest state acceptable?

## Compatible cases

- the Shadow Ledger file does not exist yet;
- an older Shadow Ledger exists but has zero events and no manifest;
- the existing manifest exactly matches the expected campaign manifest.

## Incompatible cases

- the ledger contains historical events but no manifest;
- the bound manifest differs;
- the manifest JSON/digest is malformed;
- the existing file is not a valid readable Shadow Ledger schema.

## Read-only requirement

This check must not call the normal Shadow Ledger connection helper because that helper intentionally enables SQLite WAL mode for runtime operation.

Preflight uses a read-only SQLite connection and never initializes or migrates schema.

## Result

A green Stage 4 preflight then means both:

1. external/canonical structural prerequisites are ready; and
2. the selected Shadow Ledger can safely continue or start the expected campaign.

It still does not prove live warmup, current target inferability, profitability, or funded execution readiness.
