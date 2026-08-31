# Observation Index

| Title | Keywords | Confidence | Status | Path |
|---|---|---|---|---|
| Historical RPC capabilities must be probed independently | bsc, rpc, archive, eth_call, eth_getLogs, http-400, provider-tier, collector | high | observation | `archive-capability-must-be-probed.md` |
| Public RPC capability depends on the client HTTP identity | bsc, rpc, user-agent, urllib, http-403, rate-limit | medium | observation | `public-rpc-client-identity-affects-capability.md` |
| Recent bootstrap must keep timestamp header search local | recent, timestamp, block, header, binary-search, pruning, rpc | high | observation | `recent-bootstrap-must-search-headers-locally.md` |
| Recent header search must distinguish overshoot from provider retention | recent, retention, pruning, rpc, eth_getLogs, authenticated-source | high | observation | `recent-header-search-must-be-retention-aware.md` |
| Chainlink proxy identity and event-emitter identity are distinct | chainlink, proxy, aggregator, AnswerUpdated, AggregatorConfirmed, oracle, anchor | high | observation | `chainlink-proxy-vs-aggregator.md` |
| Source-native order must break availability-timestamp ties | alignment, timestamp, aggregate_trade_id, ordering, as-of, binance | high | observation | `source-native-order-breaks-timestamp-ties.md` |
| Evidence persistence failure is not analytical failure | evidence, artifact, provenance, ci, persistence, recovery | high | observation | `evidence-persist-failure-is-not-analytical-failure.md` |
| One-day economic robustness is not alpha proof | sensitivity, ablation, brier, pnl, robustness, feature-value | high | observation | `one-day-economic-robustness-is-not-alpha-proof.md` |
