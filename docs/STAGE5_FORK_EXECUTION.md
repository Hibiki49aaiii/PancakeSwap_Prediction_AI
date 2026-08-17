# Stage 5 — Fork-only execution validation

Stage 5 exists to validate transaction lifecycle behavior without deploying user capital.
It is not a live-trading stage and it does not authorize mainnet transaction broadcast.

## Hard safety boundary

- Transaction-capable RPC is restricted to `LocalForkRpcClient`-style adapters that explicitly report `fork_only = True`.
- `LocalForkRpcClient` rejects every non-loopback endpoint. `localhost`, `127.0.0.0/8`, and other loopback addresses are the only accepted transaction RPC targets.
- No private key, seed phrase, mnemonic, raw-transaction signer, or wallet-unlock API is used.
- Fork transactions use local-node account impersonation and `eth_sendTransaction` only.
- The execution coordinator rejects a transaction adapter unless it is explicitly fork-only.
- There is no Stage 5 path that accepts the historical/mainnet `BSC_RPC_URL` for transaction submission.

## Durable intent lifecycle

Every attempted action is persisted before submission. The state machine is:

`CREATED → RESERVED → SUBMITTING → SUBMITTED → MINED → FINALIZED`

Failure/recovery states are:

- `UNKNOWN`: the RPC transport failed, or an application response such as `nonce too low` / `already known` means submission may already have taken effect. Never automatically resend.
- `RETRYABLE`: reconciliation proves the reserved nonce is still unconsumed on the local fork. The same nonce may be retried.
- `CONSUMED_UNKNOWN`: the nonce was consumed but the intended transaction cannot be identified. Fail closed.
- `REORGED`: a previously observed receipt is no longer in the canonical fork chain.
- `FAILED`: deterministic rejection that does not imply prior acceptance, or a reverted canonical receipt.

If the process stops after `SUBMITTING` is durably recorded but before an outcome is recorded, reconciliation closes the interrupted attempt journal before deciding whether the same nonce is retryable or must fail closed as consumed/unknown.

The attempt journal is append-only at the semantic level: each submission attempt receives a monotonically increasing attempt number and retains its outcome.

## Nonce rules

- Nonce is explicitly reserved from the pending account nonce before submission.
- The SQLite ledger enforces one reserved nonce per sender.
- Nonce reservation is permitted only from `CREATED`, `RETRYABLE`, or already-`RESERVED` state.
- A transport/application-unknown outcome is not retried until reconciliation checks the pending nonce.
- If the nonce is still free, the same nonce—not a newly allocated nonce—is used for retry.
- If the nonce has advanced and the original transaction cannot be found, the intent becomes `CONSUMED_UNKNOWN` and no retry is permitted.

These rules are intended to prevent a classic double-send failure after an ambiguous RPC timeout or ambiguous node response.

## Pancake Prediction bet intent

Fork testing supports only the minimal payable Prediction calls required to exercise the transaction lifecycle:

- `betBull(uint256 epoch)`
- `betBear(uint256 epoch)`

The intent contains:

- Prediction market contract address from the repository market registry,
- encoded function selector + `uint256` epoch,
- explicit stake in wei as transaction value,
- sender address,
- semantic idempotency key.

The idempotency key is scoped to `market + sender + epoch`, intentionally excluding side. Therefore the same wallet cannot create both a Bull and Bear intent for the same market round through the Stage 5 builder. Re-requesting an identical intent is idempotent; changing side or stake under the same key is rejected.

## CLI workflow

The CLI is installed as `pcs-prediction`. The local fork node itself must already be running on a loopback address; how that node obtains its upstream BSC state is deliberately outside the transaction RPC boundary.

Prepare an impersonated test account and give it fork-only balance:

```bash
pcs-prediction fork-prepare-account \
  --fork-rpc-url http://127.0.0.1:8545 \
  --sender 0x1111111111111111111111111111111111111111 \
  --balance-wei 1000000000000000000
```

Create a durable intent without sending anything:

```bash
pcs-prediction fork-create-bet-intent \
  --db artifacts/fork-execution.sqlite3 \
  --market BNBUSD \
  --sender 0x1111111111111111111111111111111111111111 \
  --epoch 123456 \
  --side bull \
  --stake-wei 1000000000000000
```

Submit the returned intent id to the loopback fork:

```bash
pcs-prediction fork-submit-intent \
  --fork-rpc-url http://127.0.0.1:8545 \
  --db artifacts/fork-execution.sqlite3 \
  --intent-id 1
```

Reconcile after mining, restart, timeout, drop, or reorg simulation:

```bash
pcs-prediction fork-reconcile-intent \
  --fork-rpc-url http://127.0.0.1:8545 \
  --db artifacts/fork-execution.sqlite3 \
  --intent-id 1 \
  --confirmations 3
```

A non-loopback `--fork-rpc-url` is rejected before any transaction call. The CLI never accepts a private key.

## Reconciliation

For a known transaction hash:

1. Check receipt.
2. If receipt exists, verify its block hash against the current canonical block at that height.
3. A successful canonical receipt is `MINED` until the configured confirmation threshold is reached, then `FINALIZED`.
4. A canonical reverted receipt is `FAILED`.
5. A receipt whose block hash no longer matches canonical history is `REORGED`.
6. If there is no receipt but `eth_getTransactionByHash` still returns the transaction, remain `SUBMITTED`.
7. If the transaction disappears, reconcile against the pending nonce before deciding whether retry is safe.

## Required adversarial tests before Stage 5 is considered complete

The automated suite must continue to cover at least:

- duplicate semantic intent rejection,
- unique nonce reservation for concurrent intents,
- ambiguous transport failure,
- ambiguous node response such as `nonce too low`,
- process interruption while `SUBMITTING`,
- restart while an intent is `UNKNOWN`,
- dropped transaction with unconsumed nonce,
- consumed nonce with unidentified transaction,
- deterministic RPC rejection,
- reverted receipt,
- receipt reorg,
- confirmation/finality transition,
- rejection of non-fork adapters,
- rejection of non-loopback transaction RPC endpoints,
- exact Pancake Bull/Bear calldata encoding,
- prevention of opposite-side intents for one wallet/round.

## Exit criteria

Stage 5 does not pass merely because unit tests are green. Before advancing to any separately approved tiny-live stage, run the lifecycle against an actual local BSC fork and demonstrate:

- successful Bull and Bear transaction inclusion on representative forked rounds,
- restart recovery with no duplicate submission,
- forced dropped/replaced transaction recovery,
- forced reorg reconciliation,
- zero unresolved intents after the test campaign,
- no transaction-capable code path pointed at a non-loopback endpoint.

Any funded or mainnet execution remains a separate legal, operational, and risk-control gate.
