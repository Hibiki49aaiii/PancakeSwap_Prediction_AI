from pathlib import Path
from typing import Any

import pytest

from pancake_prediction.execution_intent import (
    ExecutionIntentStore,
    ForkExecutionCoordinator,
    IntentState,
)
from pancake_prediction.rpc import RpcError, RpcResponseError

SENDER = "0x" + "11" * 20
TARGET = "0x" + "22" * 20
TX_HASH = "0x" + "aa" * 32


class _FakeForkRpc:
    def __init__(self) -> None:
        self.pending_nonce = 7
        self.head = 100
        self.send_calls = 0
        self.send_error: RpcError | None = None
        self.next_tx_hash = TX_HASH
        self.receipts: dict[str, dict[str, Any]] = {}
        self.transactions: dict[str, dict[str, Any]] = {}
        self.blocks: dict[int, dict[str, Any]] = {}

    @property
    def fork_only(self) -> bool:
        return True

    def transaction_count(self, address: str, tag: str = "pending") -> int:
        del address, tag
        return self.pending_nonce

    def transaction_receipt(self, tx_hash: str) -> dict[str, Any] | None:
        return self.receipts.get(tx_hash)

    def transaction_by_hash(self, tx_hash: str) -> dict[str, Any] | None:
        return self.transactions.get(tx_hash)

    def block_number(self) -> int:
        return self.head

    def block(self, number: int) -> dict[str, Any]:
        return self.blocks[number]

    def send_transaction(
        self,
        *,
        from_address: str,
        to: str,
        data: str,
        value_wei: int,
        gas: int | None = None,
        gas_price_wei: int | None = None,
        nonce: int | None = None,
    ) -> str:
        del from_address, to, data, value_wei, gas, gas_price_wei
        self.send_calls += 1
        if self.send_error is not None:
            raise self.send_error
        if nonce is None:
            raise AssertionError("coordinator must reserve an explicit nonce")
        self.pending_nonce = max(self.pending_nonce, nonce + 1)
        self.transactions[self.next_tx_hash] = {"nonce": hex(nonce)}
        return self.next_tx_hash


class _UnsafeFakeForkRpc(_FakeForkRpc):
    @property
    def fork_only(self) -> bool:
        return False


def _store(tmp_path: Path) -> ExecutionIntentStore:
    store = ExecutionIntentStore(tmp_path / "execution.sqlite3")
    store.initialize()
    return store


def _intent(store: ExecutionIntentStore, key: str = "BNBUSD:123:BULL") -> int:
    return store.get_or_create(
        idempotency_key=key,
        sender=SENDER,
        target=TARGET,
        calldata="0x1234",
        value_wei=10**15,
    ).id


def test_coordinator_rejects_non_fork_adapter(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(ValueError, match="fork-only"):
        ForkExecutionCoordinator(store, _UnsafeFakeForkRpc())


def test_idempotency_key_returns_same_intent_and_rejects_payload_change(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = _intent(store)
    second = _intent(store)
    assert first == second

    with pytest.raises(ValueError, match="different payload"):
        store.get_or_create(
            idempotency_key="BNBUSD:123:BULL",
            sender=SENDER,
            target=TARGET,
            calldata="0x9999",
            value_wei=10**15,
        )


def test_nonce_reservations_are_unique_for_same_sender(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.reserve_nonce(_intent(store, "a"), 7)
    second = store.reserve_nonce(_intent(store, "b"), 7)
    assert first.nonce == 7
    assert second.nonce == 8


def test_transport_unknown_blocks_duplicate_submit_until_nonce_reconciled(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rpc = _FakeForkRpc()
    rpc.send_error = RpcError("connection dropped after request write")
    coordinator = ForkExecutionCoordinator(store, rpc)
    intent_id = _intent(store)

    result = coordinator.submit(intent_id)
    assert result.state == IntentState.UNKNOWN
    assert result.nonce == 7
    assert rpc.send_calls == 1

    with pytest.raises(ValueError, match="duplicate submission"):
        coordinator.submit(intent_id)
    assert rpc.send_calls == 1

    rpc.send_error = None
    rpc.pending_nonce = 7
    retryable = coordinator.reconcile(intent_id)
    assert retryable.state == IntentState.RETRYABLE

    resubmitted = coordinator.submit(intent_id)
    assert resubmitted.state == IntentState.SUBMITTED
    assert resubmitted.nonce == 7
    assert resubmitted.attempts == 2
    assert rpc.send_calls == 2


def test_unknown_with_consumed_nonce_fails_closed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rpc = _FakeForkRpc()
    rpc.send_error = RpcError("transport outcome unknown")
    coordinator = ForkExecutionCoordinator(store, rpc)
    intent_id = _intent(store)

    assert coordinator.submit(intent_id).state == IntentState.UNKNOWN
    rpc.pending_nonce = 8
    result = coordinator.reconcile(intent_id)
    assert result.state == IntentState.CONSUMED_UNKNOWN

    with pytest.raises(ValueError, match="duplicate submission"):
        coordinator.submit(intent_id)


def test_deterministic_rpc_rejection_is_terminal_failed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rpc = _FakeForkRpc()
    rpc.send_error = RpcResponseError(
        "eth_sendTransaction",
        {"code": -32000, "message": "execution reverted"},
    )
    result = ForkExecutionCoordinator(store, rpc).submit(_intent(store))
    assert result.state == IntentState.FAILED
    assert result.attempts == 1


def test_dropped_known_transaction_becomes_retryable_with_same_nonce(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rpc = _FakeForkRpc()
    coordinator = ForkExecutionCoordinator(store, rpc)
    intent_id = _intent(store)
    submitted = coordinator.submit(intent_id)
    assert submitted.state == IntentState.SUBMITTED
    assert submitted.nonce == 7

    rpc.transactions.clear()
    rpc.pending_nonce = 7
    result = coordinator.reconcile(intent_id)
    assert result.state == IntentState.RETRYABLE
    assert result.nonce == 7


def test_pending_transaction_remains_submitted(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rpc = _FakeForkRpc()
    coordinator = ForkExecutionCoordinator(store, rpc)
    intent_id = _intent(store)
    coordinator.submit(intent_id)

    result = coordinator.reconcile(intent_id)
    assert result.state == IntentState.SUBMITTED


def test_receipt_moves_mined_to_finalized_and_detects_reorg(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rpc = _FakeForkRpc()
    coordinator = ForkExecutionCoordinator(store, rpc, confirmations=3)
    intent_id = _intent(store)
    coordinator.submit(intent_id)

    block_hash = "0x" + "bb" * 32
    rpc.receipts[TX_HASH] = {
        "status": "0x1",
        "blockNumber": "0x64",
        "blockHash": block_hash,
    }
    rpc.blocks[100] = {"hash": block_hash}
    rpc.head = 100
    mined = coordinator.reconcile(intent_id)
    assert mined.state == IntentState.MINED
    assert mined.receipt_block_number == 100

    rpc.blocks[100] = {"hash": "0x" + "cc" * 32}
    reorged = coordinator.reconcile(intent_id)
    assert reorged.state == IntentState.REORGED

    rpc.blocks[100] = {"hash": block_hash}
    rpc.head = 102
    finalized = coordinator.reconcile(intent_id)
    assert finalized.state == IntentState.FINALIZED


def test_reverted_receipt_is_failed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rpc = _FakeForkRpc()
    coordinator = ForkExecutionCoordinator(store, rpc)
    intent_id = _intent(store)
    coordinator.submit(intent_id)

    block_hash = "0x" + "bb" * 32
    rpc.receipts[TX_HASH] = {
        "status": "0x0",
        "blockNumber": "0x64",
        "blockHash": block_hash,
    }
    rpc.blocks[100] = {"hash": block_hash}
    result = coordinator.reconcile(intent_id)
    assert result.state == IntentState.FAILED


def test_restart_reads_durable_unknown_state_and_does_not_resubmit(tmp_path: Path) -> None:
    path = tmp_path / "execution.sqlite3"
    store = ExecutionIntentStore(path)
    store.initialize()
    rpc = _FakeForkRpc()
    rpc.send_error = RpcError("outcome unknown")
    intent_id = _intent(store)
    assert ForkExecutionCoordinator(store, rpc).submit(intent_id).state == IntentState.UNKNOWN

    restarted_store = ExecutionIntentStore(path)
    restarted_store.initialize()
    restarted = restarted_store.get(intent_id)
    assert restarted.state == IntentState.UNKNOWN
    assert restarted.nonce == 7

    with pytest.raises(ValueError, match="duplicate submission"):
        ForkExecutionCoordinator(restarted_store, rpc).submit(intent_id)


def test_submission_attempt_journal_preserves_unknown_then_success(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rpc = _FakeForkRpc()
    rpc.send_error = RpcError("unknown")
    coordinator = ForkExecutionCoordinator(store, rpc)
    intent_id = _intent(store)
    coordinator.submit(intent_id)

    rpc.pending_nonce = 7
    coordinator.reconcile(intent_id)
    rpc.send_error = None
    coordinator.submit(intent_id)

    attempts = store.attempts(intent_id)
    assert [row["outcome"] for row in attempts] == ["unknown", "submitted"]
    assert attempts[1]["tx_hash"] == TX_HASH
