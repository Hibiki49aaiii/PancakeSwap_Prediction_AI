# Recent header search must distinguish overshoot from provider retention

Status: observation
Confidence: high
Observed: 2026-08-22
Original source run: `32503882364`
Post-fix public source run: `32579584466`
Authenticated-source requirement run: `32583688315`
Related audit commit: `9abde60af8519eabe180a19fed6ebae8365f31ae`
Retention fix commit: `15b657cc418fec1e8b7cc6b32dcb0bc4f399581a`
Moving-retention fix commit: `d478e21f91a870c3200ef843090fa802c39c1bb2`
Authenticated workflow commit: `acd2a66337e3275a26f313dbcf50fc9cd1038cf8`

## Observation

A head-local exponential timestamp-to-block search can fail incorrectly on a pruned recent-header provider if its next doubled probe overshoots past the retention boundary before the requested timestamp is resolved.

For Aug 16–19 BNBUSD, the exact requested start block is `116172651` at timestamp `1786838400`. The original 48.club attempt had already read block `116216724`, which is 5h30m39s after the requested start, then jumped to `115168148` and received `block not found`. That failure combined a code-level overshoot with provider pruning.

The retention-aware resolver now searches the unavailable/available boundary first. It resumes timestamp search only when the first available header is old enough for the requested timestamp; otherwise it fails closed as `PROVIDER_RETENTION`. Unrelated HTTP/RPC failures are not reclassified.

## Moving retention edge

Post-fix public run `32579584466` exposed a second timing edge: 48.club's first retained block can advance while boundary search is running. A boundary block that had just been readable disappeared when the resolver re-read it after binary-search convergence (`block not found: 116290181`).

The resolver therefore preserves the timestamp from the last successful boundary read instead of re-reading the exact retention edge. This prevents a moving retention window from turning a valid structured retention diagnosis back into an unstructured RPC failure. It does not make 48.club capable of serving Aug 16; the measured retention boundary remains newer than the requested start.

## BNB dataseed provider-log limit

The independent audit and post-fix run established that BNB public dataseed `eth_getLogs -32005: limit exceeded` survives adaptive range halving down to a single block and a single topic for Prediction `NewOracle`, proxy `AggregatorConfirmed`, and aggregator `AnswerUpdated`. This also reproduces on current blocks.

The RPC layer preserves address, block range, topic set, JSON-RPC code, and single-block status. A terminal single-block `-32005` is `PROVIDER_LOG_LIMIT`; it is not treated as HTTP rate limiting and is not bypassed by dropping route/event completeness checks.

## Public three-day source decision

Run `32579584466` showed that the current unauthenticated source set cannot complete the Aug 16–19 gate:

- 48.club: retention window newer than the target and moving during the probe;
- BNB dataseed family / NodeReal public: terminal single-block `PROVIDER_LOG_LIMIT`;
- other attempted public routes: HTTP 403, 429, 521, or equivalent external capability failure.

Repeated public-only retries therefore add no evidence. The Three-day workflow now requires an authenticated/log-capable source and accepts, in order, `BSC_LOG_RPC_URL` then `BSC_ARCHIVE_RPC_URL`. Raw secret URLs are not persisted; Evidence stores only symbolic labels such as `env:BSC_LOG_RPC_URL`.

Authenticated-source run `32583688315` had no configured candidate and correctly failed fast with `AUTHENTICATED_RPC_REQUIRED`, `attempts=[]`, and no public fallback. This is a configuration blocker, not source success.

## Evidence boundary

- Correct retention handling is software-correctness evidence, not proof that a provider retains the requested window.
- `PROVIDER_RETENTION` and `PROVIDER_LOG_LIMIT` are successful diagnoses of unavailable sources, not source-gate success.
- `AUTHENTICATED_RPC_REQUIRED` means the public-source search space used here is exhausted for this fixed window; it does not prove any particular paid provider will satisfy the gate.
- Using `BSC_ARCHIVE_RPC_URL` for this recent three-day log gate does not by itself satisfy the separate full deployment-era historical-source gate.
- None of these findings provide profitability evidence or alter signing/live-broadcast safety boundaries.
