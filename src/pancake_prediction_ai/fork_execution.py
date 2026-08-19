from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from eth_hash.auto import keccak

from .abi_codec import decode_result, encode_call
from .fork_harness import ForkProbeResult, RpcCall
from .pancake_contract import PredictionRoundState, parse_round_result


ROUND_OUTPUT_TYPES = (
    "uint256",
    "uint256",
    "uint256",
    "uint256",
    "int256",
    "int256",
    "uint256",
    "uint256",
    "uint256",
    "uint256",
    "uint256",
    "uint256",
    "uint256",
    "bool",
)

BULL_TEST_ACCOUNT = "0x000000000000000000000000000000000000b001"
BEAR_TEST_ACCOUNT = "0x000000000000000000000000000000000000b002"
DEFAULT_GAS_LIMIT = 500_000
DEFAULT_MIN_WINDOW_MARGIN_SECONDS = 3


class Stage5BExecutionBlocked(RuntimeError):
    """The verified fork is valid but its current round is unsuitable for the probe."""


@dataclass(frozen=True, slots=True)
class Stage5BExecutionResult:
    prediction_contract: str
    fork_base_block: int
    fork_base_block_hash: str
    epoch: int
    block_timestamp_s: int
    round_start_timestamp_s: int
    round_lock_timestamp_s: int
    min_bet_amount_wei: int
    stake_wei: int
    bull_test_account: str
    bear_test_account: str
    bettable_window_observed: bool
    bull_tx_hash: str
    bull_receipt_block: int
    bull_tx_mined_success: bool
    bull_event_observed: bool
    bull_ledger_matches: bool
    bull_pool_delta_matches: bool
    duplicate_bull_reverted: bool
    state_restored_after_bull_reset: bool
    below_minimum_bear_reverted: bool
    bear_tx_hash: str
    bear_receipt_block: int
    bear_tx_mined_success: bool
    bear_event_observed: bool
    bear_ledger_matches: bool
    bear_pool_delta_matches: bool
    state_restored_after_bear_reset: bool
    private_key_used: bool = False
    raw_signed_transaction_used: bool = False
    mainnet_transaction_broadcast: bool = False

    @property
    def passed(self) -> bool:
        return (
            self.bettable_window_observed
            and self.min_bet_amount_wei > 0
            and self.stake_wei >= self.min_bet_amount_wei
            and self.bull_tx_mined_success
            and self.bull_event_observed
            and self.bull_ledger_matches
            and self.bull_pool_delta_matches
            and self.duplicate_bull_reverted
            and self.state_restored_after_bull_reset
            and self.below_minimum_bear_reverted
            and self.bear_tx_mined_success
            and self.bear_event_observed
            and self.bear_ledger_matches
            and self.bear_pool_delta_matches
            and self.state_restored_after_bear_reset
            and not self.private_key_used
            and not self.raw_signed_transaction_used
            and not self.mainnet_transaction_broadcast
        )


def _hex_int(value: object, *, field: str) -> int:
    if not isinstance(value, str) or not value.startswith("0x"):
        raise ValueError(f"{field} must be a hex integer")
    try:
        return int(value, 16)
    except ValueError as exc:
        raise ValueError(f"{field} must be hexadecimal") from exc


def _hex_bytes32(value: object, *, field: str) -> str:
    if not isinstance(value, str) or len(value) != 66 or not value.startswith("0x"):
        raise ValueError(f"{field} must be 32-byte hex")
    try:
        int(value[2:], 16)
    except ValueError as exc:
        raise ValueError(f"{field} must be hexadecimal") from exc
    return value.lower()


def _latest_block(rpc: RpcCall) -> tuple[int, str, int]:
    raw = rpc("eth_getBlockByNumber", ["latest", False])
    if not isinstance(raw, dict):
        raise ValueError("latest block response must be an object")
    number = _hex_int(raw.get("number"), field="block.number")
    block_hash = _hex_bytes32(raw.get("hash"), field="block.hash")
    timestamp = _hex_int(raw.get("timestamp"), field="block.timestamp")
    return number, block_hash, timestamp


def _call(rpc: RpcCall, *, to: str, data: str, from_address: str | None = None, value_wei: int | None = None) -> str:
    transaction: dict[str, str] = {"to": to, "data": data}
    if from_address is not None:
        transaction["from"] = from_address
    if value_wei is not None:
        transaction["value"] = hex(value_wei)
    raw = rpc("eth_call", [transaction, "latest"])
    if not isinstance(raw, str) or not raw.startswith("0x"):
        raise ValueError("eth_call result must be hex data")
    return raw


def _read_uint(rpc: RpcCall, contract: str, signature: str) -> int:
    raw = _call(rpc, to=contract, data=encode_call(signature))
    values = decode_result(raw, ("uint256",))
    return int(values[0])


def _read_round(rpc: RpcCall, contract: str, epoch: int) -> PredictionRoundState:
    raw = _call(
        rpc,
        to=contract,
        data=encode_call(
            "rounds(uint256)",
            argument_types=("uint256",),
            arguments=(epoch,),
        ),
    )
    return parse_round_result(decode_result(raw, ROUND_OUTPUT_TYPES))


def _read_ledger(rpc: RpcCall, contract: str, epoch: int, account: str) -> tuple[int, int, bool]:
    raw = _call(
        rpc,
        to=contract,
        data=encode_call(
            "ledger(uint256,address)",
            argument_types=("uint256", "address"),
            arguments=(epoch, account),
        ),
    )
    position, amount, claimed = decode_result(raw, ("uint8", "uint256", "bool"))
    return int(position), int(amount), bool(claimed)


def _bet_data(side: str, epoch: int) -> str:
    if side == "BULL":
        signature = "betBull(uint256)"
    elif side == "BEAR":
        signature = "betBear(uint256)"
    else:
        raise ValueError("side must be BULL or BEAR")
    return encode_call(signature, argument_types=("uint256",), arguments=(epoch,))


def _set_balance(rpc: RpcCall, account: str, balance_wei: int) -> None:
    if balance_wei <= 0:
        raise ValueError("balance_wei must be positive")
    result = rpc("anvil_setBalance", [account, hex(balance_wei)])
    if result not in {None, True}:
        raise RuntimeError("anvil_setBalance was not acknowledged")


def _impersonate(rpc: RpcCall, account: str) -> None:
    result = rpc("anvil_impersonateAccount", [account])
    if result not in {None, True}:
        raise RuntimeError("anvil_impersonateAccount was not acknowledged")


def _stop_impersonating(rpc: RpcCall, account: str) -> None:
    result = rpc("anvil_stopImpersonatingAccount", [account])
    if result not in {None, True}:
        raise RuntimeError("anvil_stopImpersonatingAccount was not acknowledged")


def _send_bet_transaction(
    rpc: RpcCall,
    *,
    contract: str,
    account: str,
    epoch: int,
    side: str,
    stake_wei: int,
    gas_limit: int,
) -> tuple[str, dict[str, Any]]:
    _impersonate(rpc, account)
    try:
        raw_hash = rpc(
            "eth_sendTransaction",
            [
                {
                    "from": account,
                    "to": contract,
                    "value": hex(stake_wei),
                    "gas": hex(gas_limit),
                    "data": _bet_data(side, epoch),
                }
            ],
        )
    finally:
        _stop_impersonating(rpc, account)
    tx_hash = _hex_bytes32(raw_hash, field="transaction hash")

    receipt = rpc("eth_getTransactionReceipt", [tx_hash])
    if receipt is None:
        rpc("evm_mine", [])
        receipt = rpc("eth_getTransactionReceipt", [tx_hash])
    if not isinstance(receipt, dict):
        raise RuntimeError("local transaction receipt was not available")
    return tx_hash, receipt


def _receipt_success(receipt: dict[str, Any]) -> tuple[bool, int]:
    status = _hex_int(receipt.get("status"), field="receipt.status")
    block_number = _hex_int(receipt.get("blockNumber"), field="receipt.blockNumber")
    return status == 1, block_number


def _event_observed(
    receipt: dict[str, Any],
    *,
    contract: str,
    side: str,
    account: str,
    epoch: int,
    amount_wei: int,
) -> bool:
    signature = "BetBull(address,uint256,uint256)" if side == "BULL" else "BetBear(address,uint256,uint256)"
    topic0 = "0x" + keccak(signature.encode("ascii")).hex()
    topic1 = "0x" + account[2:].lower().rjust(64, "0")
    topic2 = "0x" + epoch.to_bytes(32, "big").hex()
    data = "0x" + amount_wei.to_bytes(32, "big").hex()
    logs = receipt.get("logs")
    if not isinstance(logs, list):
        return False
    for log in logs:
        if not isinstance(log, dict):
            continue
        address = log.get("address")
        topics = log.get("topics")
        log_data = log.get("data")
        if (
            isinstance(address, str)
            and address.lower() == contract.lower()
            and isinstance(topics, list)
            and len(topics) >= 3
            and all(isinstance(value, str) for value in topics[:3])
            and str(topics[0]).lower() == topic0
            and str(topics[1]).lower() == topic1
            and str(topics[2]).lower() == topic2
            and isinstance(log_data, str)
            and log_data.lower() == data
        ):
            return True
    return False


def _call_reverts(
    rpc: RpcCall,
    *,
    contract: str,
    account: str,
    data: str,
    value_wei: int,
) -> bool:
    try:
        _call(
            rpc,
            to=contract,
            data=data,
            from_address=account,
            value_wei=value_wei,
        )
    except RuntimeError:
        return True
    return False


def _reset_and_verify_baseline(
    rpc: RpcCall,
    *,
    fork_result: ForkProbeResult,
    epoch: int,
    round_before: PredictionRoundState,
    account: str,
) -> bool:
    reset = rpc("anvil_reset", [])
    if reset not in {None, True}:
        return False
    block_number, block_hash, _ = _latest_block(rpc)
    if block_number != fork_result.initial_block:
        return False
    if block_hash != str(fork_result.upstream_fork_block_hash).lower():
        return False
    round_after_reset = _read_round(rpc, fork_result.prediction_contract, epoch)
    ledger_after_reset = _read_ledger(rpc, fork_result.prediction_contract, epoch, account)
    return round_after_reset == round_before and ledger_after_reset[1] == 0 and ledger_after_reset[2] is False


def run_stage5b_prediction_execution_probe(
    rpc: RpcCall,
    *,
    fork_result: ForkProbeResult,
    stake_wei: int | None = None,
    gas_limit: int = DEFAULT_GAS_LIMIT,
    min_window_margin_seconds: int = DEFAULT_MIN_WINDOW_MARGIN_SECONDS,
) -> Stage5BExecutionResult:
    """Exercise real Prediction bet paths on an already verified local BSC fork.

    The function uses deterministic impersonated EOAs and `eth_sendTransaction`
    against a loopback development node. No private key or raw signed transaction
    is accepted. Both successful BULL/BEAR mutations are reset back to the fork
    base, and the final local state must equal the original baseline.
    """

    if not fork_result.verified_passed:
        raise ValueError("Stage 5B execution requires a verified fork result")
    if gas_limit <= 0:
        raise ValueError("gas_limit must be positive")
    if min_window_margin_seconds < 1:
        raise ValueError("min_window_margin_seconds must be >= 1")

    contract = fork_result.prediction_contract
    base_number, base_hash, block_timestamp = _latest_block(rpc)
    if base_number != fork_result.initial_block:
        raise ValueError("local fork moved from verified base before execution probe")
    if base_hash != str(fork_result.upstream_fork_block_hash).lower():
        raise ValueError("local fork base hash no longer matches verified upstream block")

    epoch = _read_uint(rpc, contract, "currentEpoch()")
    min_bet = _read_uint(rpc, contract, "minBetAmount()")
    if min_bet <= 0:
        raise Stage5BExecutionBlocked("Prediction minBetAmount must be positive")
    stake = min_bet if stake_wei is None else stake_wei
    if stake < min_bet:
        raise ValueError("stake_wei must be >= current minBetAmount")

    round_before = _read_round(rpc, contract, epoch)
    if round_before.epoch != epoch:
        raise Stage5BExecutionBlocked("currentEpoch round state is not initialized")
    bettable = (
        round_before.start_timestamp > 0
        and round_before.lock_timestamp > 0
        and block_timestamp > round_before.start_timestamp
        and block_timestamp + min_window_margin_seconds < round_before.lock_timestamp
    )
    if not bettable:
        raise Stage5BExecutionBlocked("current fork-base round is not safely inside its betting window")

    bull_before = _read_ledger(rpc, contract, epoch, BULL_TEST_ACCOUNT)
    bear_before = _read_ledger(rpc, contract, epoch, BEAR_TEST_ACCOUNT)
    if bull_before[1] != 0 or bear_before[1] != 0:
        raise Stage5BExecutionBlocked("deterministic probe account already has a bet in current epoch")

    balance = max(10**18, stake * 100, stake + 10**17)
    _set_balance(rpc, BULL_TEST_ACCOUNT, balance)
    _set_balance(rpc, BEAR_TEST_ACCOUNT, balance)

    below_minimum_bear_reverted = _call_reverts(
        rpc,
        contract=contract,
        account=BEAR_TEST_ACCOUNT,
        data=_bet_data("BEAR", epoch),
        value_wei=min_bet - 1,
    )

    bull_tx_hash, bull_receipt = _send_bet_transaction(
        rpc,
        contract=contract,
        account=BULL_TEST_ACCOUNT,
        epoch=epoch,
        side="BULL",
        stake_wei=stake,
        gas_limit=gas_limit,
    )
    bull_success, bull_receipt_block = _receipt_success(bull_receipt)
    bull_event = _event_observed(
        bull_receipt,
        contract=contract,
        side="BULL",
        account=BULL_TEST_ACCOUNT,
        epoch=epoch,
        amount_wei=stake,
    )
    bull_position, bull_amount, bull_claimed = _read_ledger(
        rpc, contract, epoch, BULL_TEST_ACCOUNT
    )
    bull_ledger_matches = bull_position == 0 and bull_amount == stake and not bull_claimed
    bull_round = _read_round(rpc, contract, epoch)
    bull_pool_delta_matches = (
        bull_round.total_amount_wei == round_before.total_amount_wei + stake
        and bull_round.bull_amount_wei == round_before.bull_amount_wei + stake
        and bull_round.bear_amount_wei == round_before.bear_amount_wei
    )
    duplicate_bull_reverted = _call_reverts(
        rpc,
        contract=contract,
        account=BULL_TEST_ACCOUNT,
        data=_bet_data("BULL", epoch),
        value_wei=stake,
    )
    bull_restored = _reset_and_verify_baseline(
        rpc,
        fork_result=fork_result,
        epoch=epoch,
        round_before=round_before,
        account=BULL_TEST_ACCOUNT,
    )

    _set_balance(rpc, BEAR_TEST_ACCOUNT, balance)
    bear_tx_hash, bear_receipt = _send_bet_transaction(
        rpc,
        contract=contract,
        account=BEAR_TEST_ACCOUNT,
        epoch=epoch,
        side="BEAR",
        stake_wei=stake,
        gas_limit=gas_limit,
    )
    bear_success, bear_receipt_block = _receipt_success(bear_receipt)
    bear_event = _event_observed(
        bear_receipt,
        contract=contract,
        side="BEAR",
        account=BEAR_TEST_ACCOUNT,
        epoch=epoch,
        amount_wei=stake,
    )
    bear_position, bear_amount, bear_claimed = _read_ledger(
        rpc, contract, epoch, BEAR_TEST_ACCOUNT
    )
    bear_ledger_matches = bear_position == 1 and bear_amount == stake and not bear_claimed
    bear_round = _read_round(rpc, contract, epoch)
    bear_pool_delta_matches = (
        bear_round.total_amount_wei == round_before.total_amount_wei + stake
        and bear_round.bear_amount_wei == round_before.bear_amount_wei + stake
        and bear_round.bull_amount_wei == round_before.bull_amount_wei
    )
    bear_restored = _reset_and_verify_baseline(
        rpc,
        fork_result=fork_result,
        epoch=epoch,
        round_before=round_before,
        account=BEAR_TEST_ACCOUNT,
    )

    return Stage5BExecutionResult(
        prediction_contract=contract,
        fork_base_block=base_number,
        fork_base_block_hash=base_hash,
        epoch=epoch,
        block_timestamp_s=block_timestamp,
        round_start_timestamp_s=round_before.start_timestamp,
        round_lock_timestamp_s=round_before.lock_timestamp,
        min_bet_amount_wei=min_bet,
        stake_wei=stake,
        bull_test_account=BULL_TEST_ACCOUNT,
        bear_test_account=BEAR_TEST_ACCOUNT,
        bettable_window_observed=bettable,
        bull_tx_hash=bull_tx_hash,
        bull_receipt_block=bull_receipt_block,
        bull_tx_mined_success=bull_success,
        bull_event_observed=bull_event,
        bull_ledger_matches=bull_ledger_matches,
        bull_pool_delta_matches=bull_pool_delta_matches,
        duplicate_bull_reverted=duplicate_bull_reverted,
        state_restored_after_bull_reset=bull_restored,
        below_minimum_bear_reverted=below_minimum_bear_reverted,
        bear_tx_hash=bear_tx_hash,
        bear_receipt_block=bear_receipt_block,
        bear_tx_mined_success=bear_success,
        bear_event_observed=bear_event,
        bear_ledger_matches=bear_ledger_matches,
        bear_pool_delta_matches=bear_pool_delta_matches,
        state_restored_after_bear_reset=bear_restored,
    )
