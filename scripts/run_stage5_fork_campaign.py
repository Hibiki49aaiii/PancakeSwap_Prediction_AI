from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from pancake_prediction.contracts import CHAIN_ID_BSC, MARKETS
from pancake_prediction.execution_intent import (
    ExecutionIntent,
    ExecutionIntentStore,
    ForkExecutionCoordinator,
    IntentState,
)
from pancake_prediction.prediction_preflight import (
    CURRENT_EPOCH_SELECTOR,
    MIN_BET_AMOUNT_SELECTOR,
    require_prediction_bet_ready,
)
from pancake_prediction.prediction_tx import BetSide, build_prediction_bet_intent
from pancake_prediction.rpc import LocalForkRpcClient
from pancake_prediction.stage5_evidence import (
    EvidenceOrigin,
    Stage5ForkEvidence,
    evaluate_stage5b_fork_gate,
    ledger_sha256,
)

MARKET = "BNBUSD"
SENDERS = {
    "bull": "0x1000000000000000000000000000000000000001",
    "bear": "0x1000000000000000000000000000000000000002",
    "restart": "0x1000000000000000000000000000000000000003",
    "drop": "0x1000000000000000000000000000000000000004",
    "reorg": "0x1000000000000000000000000000000000000005",
}


def _decode_uint256(result: str) -> int:
    raw = result.removeprefix("0x")
    if len(raw) != 64:
        raise RuntimeError("expected one uint256 ABI word")
    return int(raw, 16)


def _read_round_inputs(rpc: LocalForkRpcClient) -> tuple[int, int]:
    target = MARKETS[MARKET].address
    epoch = _decode_uint256(rpc.eth_call(target, CURRENT_EPOCH_SELECTOR))
    min_bet = _decode_uint256(rpc.eth_call(target, MIN_BET_AMOUNT_SELECTOR))
    if min_bet <= 0:
        raise RuntimeError("Prediction minBetAmount must be positive")
    return epoch, min_bet


def _prepare_senders(rpc: LocalForkRpcClient, *, balance_wei: int) -> None:
    for sender in SENDERS.values():
        rpc.impersonate_account(sender)
        rpc.set_balance(sender, balance_wei)


def _create_intent(
    store: ExecutionIntentStore,
    *,
    sender: str,
    epoch: int,
    side: BetSide,
    stake_wei: int,
) -> ExecutionIntent:
    return build_prediction_bet_intent(
        store,
        market=MARKET,
        sender=sender,
        epoch=epoch,
        side=side,
        stake_wei=stake_wei,
    )


def _finalize(
    coordinator: ForkExecutionCoordinator,
    intent_id: int,
    *,
    max_extra_blocks: int = 4,
) -> ExecutionIntent:
    current = coordinator.reconcile(intent_id)
    for _ in range(max_extra_blocks):
        if current.state == IntentState.FINALIZED:
            return current
        if current.state != IntentState.MINED:
            raise RuntimeError(
                f"intent {intent_id} did not reach a mineable state: {current.state}"
            )
        coordinator.rpc.mine()
        current = coordinator.reconcile(intent_id)
    raise RuntimeError(f"intent {intent_id} did not finalize")


def _submit_and_finalize(
    store: ExecutionIntentStore,
    rpc: LocalForkRpcClient,
    intent: ExecutionIntent,
    *,
    confirmations: int = 1,
) -> ExecutionIntent:
    require_prediction_bet_ready(rpc, intent)
    coordinator = ForkExecutionCoordinator(store, rpc, confirmations=confirmations)
    submitted = coordinator.submit(intent.id)
    if submitted.state != IntentState.SUBMITTED:
        raise RuntimeError(
            f"intent {intent.id} submission did not become SUBMITTED: {submitted.state}"
        )
    return _finalize(coordinator, intent.id)


def _observe_restart_recovery(
    path: Path,
    store: ExecutionIntentStore,
    rpc: LocalForkRpcClient,
    *,
    epoch: int,
    stake_wei: int,
) -> bool:
    intent = _create_intent(
        store,
        sender=SENDERS["restart"],
        epoch=epoch,
        side=BetSide.BULL,
        stake_wei=stake_wei,
    )
    pending_nonce = rpc.transaction_count(intent.sender, "pending")
    store.reserve_nonce(intent.id, pending_nonce)
    store.begin_submission(intent.id)

    restarted_store = ExecutionIntentStore(path)
    restarted_store.initialize()
    recovered = ForkExecutionCoordinator(restarted_store, rpc).reconcile(intent.id)
    attempts = restarted_store.attempts(intent.id)
    observed = (
        recovered.state == IntentState.RETRYABLE
        and len(attempts) == 1
        and str(attempts[0]["outcome"]) == "interrupted"
    )
    restarted_store.set_reconciliation_state(
        intent.id,
        IntentState.FAILED,
        error="campaign interruption was recovered before any transaction was broadcast",
    )
    return observed


def _observe_drop_recovery(
    store: ExecutionIntentStore,
    rpc: LocalForkRpcClient,
    *,
    epoch: int,
    stake_wei: int,
) -> bool:
    intent = _create_intent(
        store,
        sender=SENDERS["drop"],
        epoch=epoch,
        side=BetSide.BULL,
        stake_wei=stake_wei,
    )
    require_prediction_bet_ready(rpc, intent)
    coordinator = ForkExecutionCoordinator(store, rpc, confirmations=1)
    rpc.set_automine(False)
    try:
        submitted = coordinator.submit(intent.id)
        if submitted.state != IntentState.SUBMITTED or submitted.current_tx_hash is None:
            raise RuntimeError("drop scenario transaction was not left pending")
        tx_hash = submitted.current_tx_hash
        rpc.drop_transaction(tx_hash)
        if rpc.transaction_by_hash(tx_hash) is not None:
            raise RuntimeError("dropped transaction is still visible in the local fork pool")
        recovered = coordinator.reconcile(intent.id)
        observed = (
            recovered.state == IntentState.RETRYABLE
            and recovered.nonce == submitted.nonce
        )
    finally:
        rpc.set_automine(True)

    if not observed:
        raise RuntimeError("dropped transaction did not recover to the same reserved nonce")
    retryable = store.get(intent.id)
    require_prediction_bet_ready(rpc, retryable)
    resubmitted = coordinator.submit(intent.id)
    if resubmitted.state != IntentState.SUBMITTED:
        raise RuntimeError("drop-recovered intent did not resubmit")
    _finalize(coordinator, intent.id)
    return True


def _observe_reorg_recovery(
    store: ExecutionIntentStore,
    rpc: LocalForkRpcClient,
    *,
    epoch: int,
    stake_wei: int,
) -> bool:
    intent = _create_intent(
        store,
        sender=SENDERS["reorg"],
        epoch=epoch,
        side=BetSide.BEAR,
        stake_wei=stake_wei,
    )
    require_prediction_bet_ready(rpc, intent)
    snapshot_id = rpc.snapshot()
    coordinator = ForkExecutionCoordinator(store, rpc, confirmations=2)
    submitted = coordinator.submit(intent.id)
    if submitted.state != IntentState.SUBMITTED or submitted.current_tx_hash is None:
        raise RuntimeError("reorg scenario transaction was not submitted")
    tx_hash = submitted.current_tx_hash
    mined = coordinator.reconcile(intent.id)
    if mined.state != IntentState.MINED:
        raise RuntimeError(f"reorg scenario did not first reach MINED: {mined.state}")

    rpc.revert(snapshot_id)
    if rpc.transaction_receipt(tx_hash) is not None:
        raise RuntimeError("reverted fork snapshot still exposes the old receipt")
    recovered = coordinator.reconcile(intent.id)
    observed = recovered.state == IntentState.RETRYABLE and recovered.nonce == mined.nonce
    if not observed:
        raise RuntimeError("reorg did not recover the original nonce as retryable")

    retryable = store.get(intent.id)
    require_prediction_bet_ready(rpc, retryable)
    resubmitted = coordinator.submit(intent.id)
    if resubmitted.state != IntentState.SUBMITTED:
        raise RuntimeError("reorg-recovered intent did not resubmit")
    _finalize(coordinator, intent.id)
    return True


def _observe_non_loopback_rejection(probe_url: str) -> bool:
    try:
        LocalForkRpcClient(probe_url)
    except ValueError as exc:
        return "loopback" in str(exc).lower()
    return False


def run_campaign(
    *,
    fork_rpc_url: str,
    db_path: Path,
    evidence_path: Path,
    source_sha: str,
    fork_block_number: int,
    anvil_version: str,
    non_loopback_probe_url: str,
) -> dict[str, object]:
    if db_path.exists():
        raise FileExistsError(f"refusing to reuse existing campaign database: {db_path}")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.parent.mkdir(parents=True, exist_ok=True)

    rpc = LocalForkRpcClient(fork_rpc_url)
    if rpc.chain_id() != CHAIN_ID_BSC:
        raise RuntimeError("local fork does not report BSC chain id 56")
    if fork_block_number <= 0 or rpc.block_number() < fork_block_number:
        raise RuntimeError("local fork head is inconsistent with the requested fork block")
    fork_block = rpc.block(fork_block_number)
    fork_block_hash = str(fork_block.get("hash", ""))
    if not fork_block_hash.startswith("0x"):
        raise RuntimeError("local fork did not expose the source fork block hash")

    store = ExecutionIntentStore(db_path)
    store.initialize()
    epoch, min_bet = _read_round_inputs(rpc)
    stake_wei = min_bet
    funding = max(stake_wei * 20, 10**18)
    _prepare_senders(rpc, balance_wei=funding)

    bull = _create_intent(
        store,
        sender=SENDERS["bull"],
        epoch=epoch,
        side=BetSide.BULL,
        stake_wei=stake_wei,
    )
    bear = _create_intent(
        store,
        sender=SENDERS["bear"],
        epoch=epoch,
        side=BetSide.BEAR,
        stake_wei=stake_wei,
    )
    _submit_and_finalize(store, rpc, bull)
    _submit_and_finalize(store, rpc, bear)

    scenarios = {
        "restart_recovery": _observe_restart_recovery(
            db_path,
            store,
            rpc,
            epoch=epoch,
            stake_wei=stake_wei,
        ),
        "dropped_or_replaced_recovery": _observe_drop_recovery(
            store,
            rpc,
            epoch=epoch,
            stake_wei=stake_wei,
        ),
        "reorg_reconciliation": _observe_reorg_recovery(
            store,
            rpc,
            epoch=epoch,
            stake_wei=stake_wei,
        ),
        "non_loopback_rejection": _observe_non_loopback_rejection(
            non_loopback_probe_url
        ),
    }
    if not all(scenarios.values()):
        raise RuntimeError(f"one or more Stage 5B scenarios were not observed: {scenarios}")

    evidence = Stage5ForkEvidence.create(
        origin=EvidenceOrigin.OBSERVED,
        source_sha=source_sha,
        recorded_at=datetime.now(UTC).isoformat(),
        campaign_id=f"stage5b-{source_sha[:12]}-{fork_block_number}",
        market=MARKET,
        chain_id=rpc.chain_id(),
        fork_block_number=fork_block_number,
        fork_block_hash=fork_block_hash,
        anvil_version=anvil_version,
        ledger_sha256=ledger_sha256(db_path),
        scenarios=scenarios,
    )
    evidence_path.write_text(
        json.dumps(evidence.as_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    gate = evaluate_stage5b_fork_gate(
        ledger_path=db_path,
        evidence=evidence,
        expected_source_sha=source_sha,
    )
    if not gate.ready:
        raise RuntimeError(f"Stage 5B gate did not clear: {gate.blockers}")
    return {
        "campaign_id": evidence.campaign_id,
        "epoch": epoch,
        "stake_wei": stake_wei,
        "fork_block_number": fork_block_number,
        "fork_block_hash": fork_block_hash,
        "anvil_version": anvil_version,
        "scenarios": scenarios,
        "gate": gate.as_dict(),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the observed Stage 5B campaign against a loopback Anvil BSC fork."
    )
    parser.add_argument("--fork-rpc-url", default="http://127.0.0.1:8545")
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--fork-block-number", type=int, required=True)
    parser.add_argument("--anvil-version", required=True)
    parser.add_argument(
        "--non-loopback-probe-url",
        default="https://bsc-dataseed.bnbchain.org",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = run_campaign(
        fork_rpc_url=str(args.fork_rpc_url),
        db_path=Path(args.db),
        evidence_path=Path(args.evidence),
        source_sha=str(args.source_sha),
        fork_block_number=int(args.fork_block_number),
        anvil_version=str(args.anvil_version),
        non_loopback_probe_url=str(args.non_loopback_probe_url),
    )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())