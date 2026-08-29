# Issue #11 Human Understanding

## What

Before starting a long Stage 4 Shadow campaign, operators can run the same runtime configuration in a read-only preflight mode.

The preflight answers whether the structural inputs and external read paths needed to start the campaign are present.

## What it checks

- canonical database exists and is readable;
- source-anchor metadata exists and is valid;
- enough settled historical rounds exist for the configured model/calibrator/pool baseline;
- enough active Chainlink history exists for the configured hazard feature;
- BSC RPC is on chain 56 and is not behind the canonical checkpoint;
- ClickHouse schema is retry-safe;
- configured Spot/Perp historical lineages contain rows;
- Binance public Spot/Perp endpoints answer a one-row read-only probe.

## What it deliberately does not do

It does not collect, ingest, initialize, predict, checkpoint campaign Evidence, sign, broadcast, or fund anything.

It also does not claim that the first live target will be inferable. Live source warmup and oracle-route stability remain runtime checks.

## Why

The next milestone is empirical long-running Stage 4 operation. Structural failures should be found before the long-running process is started, while keeping the live runtime's fail-closed checks authoritative.
