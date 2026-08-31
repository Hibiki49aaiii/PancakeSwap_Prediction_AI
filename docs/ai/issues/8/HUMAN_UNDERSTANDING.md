# Issue #8 Human Understanding

## What

Stage 4 used to calculate pool-growth projections for every historical round even though the live decision only needs one current target.

The new path calculates only the requested target.

## Why results stay identical

The actual target-projection algorithm is not duplicated or rewritten independently. The existing logic is moved into a shared internal function used by both the historical all-target builder and the live single-target builder.

The same prior rounds, purge rule, settlement cutoff, median growth calculation and model identity are used.

## Additional optimization

A single event index is reused while calculating target/prior snapshots instead of rebuilding event lookup structures for each historical prior round.

## Safety

No signing, broadcasting, wallet, funded execution or profitability promotion is added.

The target's final pool and final outcome remain forbidden inputs.
