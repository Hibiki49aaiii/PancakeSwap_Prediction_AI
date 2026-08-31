# Recent canonical public-RPC path

This path exists to keep research moving when an archive-capable BSC state RPC is not configured.
It does **not** replace the full-history canonical gate.

## Evidence boundary

The recent bootstrap reads only:

- canonical BSC block headers;
- canonical PancakeSwap Prediction event logs;
- no historical `eth_call` state at the deployment era;
- no signer, wallet unlock, private key, or transaction broadcast.

Prediction `StartRound`, `LockRound`, `EndRound`, bet, and protocol events are authoritative on-chain
Prediction evidence when collected from the canonical chain and reconciled by the event store.

The recent public-RPC path intentionally does **not** collect Chainlink feed events. Direction labels and
final pools come from Prediction settlement events, but Chainlink freshness/oracle-latency feature families
are unavailable and must remain excluded from the model.

## Leakage rules

A recent CEX-only research row may use only information available before the target decision cutoff:

- Binance Spot order flow;
- Binance USD-M Futures order flow;
- Spot/Perp basis;
- outcomes/returns from older rounds that fully settled before the target decision;
- no target final pool;
- no target lock/close settlement price;
- no unavailable Chainlink-derived field;
- no observed future `LockRound` timestamp as the decision cutoff.

Decision timing is derived with the same `build_decision_snapshot` logic as the canonical backtester.

## Economic interpretation

The recent economic benchmark may use the target round's canonical final Bull/Bear pools only **after** the
hypothetical decision to calculate realized pari-mutuel PnL. Expected value must use a separately trained,
target-blind pool projection.

Stake, treasury fee, bet gas, claim gas, inclusion latency, decision lead, and purge settings are explicit
scenario inputs. A result produced with assumed gas or latency is a scenario result, not measured live
execution evidence.

Every report must state:

- `authoritative_prediction_events=true` when the event collection passed canonical checks;
- `chainlink_collected=false` for this path;
- `profitability_gate_eligible=false` until the missing evidence is supplied.

## Relationship to full-history validation

The full-history path remains stronger because it binds:

1. deployment-era canonical Prediction history;
2. exact active-Chainlink history;
3. checksum-verified Binance source slices;
4. purged/embargoed OOS modelling and calibration;
5. target-blind pool projection;
6. explicit fee/gas/latency economics;
7. sensitivity analysis and later no-signing shadow evidence.

An authenticated archive-capable BSC endpoint can still be supplied through the repository secret
`BSC_ARCHIVE_RPC_URL`. The recent path is additive evidence, not a bypass around that gate.
