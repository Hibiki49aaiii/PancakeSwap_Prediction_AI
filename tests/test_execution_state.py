from __future__ import annotations

import pytest

from pancake_prediction_ai.execution_state import ExecutionIntent, IntentState, reconcile_receipt


def base_intent() -> ExecutionIntent:
    return ExecutionIntent(intent_id="r42-bull", round_id=42, side="BULL", amount_wei=10**15)


def test_happy_path_reaches_finalized() -> None:
    intent = base_intent().transition(IntentState.RESERVED, nonce=7)
    intent = intent.transition(IntentState.SUBMITTED, tx_hash="0xabc")
    intent = reconcile_receipt(
        intent,
        receipt_present=True,
        canonical_block=True,
        block_number=100,
        confirmations=3,
        required_confirmations=2,
    )
    assert intent.state is IntentState.FINALIZED
    assert intent.terminal


def test_timeout_after_submit_becomes_unknown_not_failed() -> None:
    intent = base_intent().transition(IntentState.RESERVED, nonce=7)
    intent = intent.transition(IntentState.SUBMITTED, tx_hash="0xabc")
    recovered = reconcile_receipt(
        intent,
        receipt_present=False,
        canonical_block=False,
        block_number=None,
        confirmations=0,
        required_confirmations=2,
    )
    assert recovered.state is IntentState.UNKNOWN
    assert recovered.unresolved


def test_reorg_moves_mined_back_to_unknown() -> None:
    intent = base_intent().transition(IntentState.RESERVED, nonce=7)
    intent = intent.transition(IntentState.SUBMITTED, tx_hash="0xabc")
    mined = reconcile_receipt(
        intent,
        receipt_present=True,
        canonical_block=True,
        block_number=100,
        confirmations=1,
        required_confirmations=3,
    )
    assert mined.state is IntentState.MINED
    reorged = reconcile_receipt(
        mined,
        receipt_present=True,
        canonical_block=False,
        block_number=100,
        confirmations=0,
        required_confirmations=3,
    )
    assert reorged.state is IntentState.UNKNOWN


def test_same_nonce_replacement_can_finalize_replacement_hash() -> None:
    intent = base_intent().transition(IntentState.RESERVED, nonce=7)
    intent = intent.transition(IntentState.SUBMITTED, tx_hash="0xold")
    intent = intent.transition(IntentState.REPLACED, replacement_tx_hash="0xnew")
    finalized = reconcile_receipt(
        intent,
        receipt_present=True,
        canonical_block=True,
        block_number=101,
        confirmations=2,
        required_confirmations=2,
    )
    assert finalized.state is IntentState.FINALIZED
    assert finalized.tx_hash == "0xnew"


def test_terminal_state_cannot_be_reopened() -> None:
    intent = base_intent().transition(IntentState.CANCELLED)
    with pytest.raises(ValueError, match="illegal transition"):
        intent.transition(IntentState.RESERVED)


def test_submitted_requires_hash() -> None:
    intent = base_intent().transition(IntentState.RESERVED, nonce=3)
    with pytest.raises(ValueError, match="requires tx_hash"):
        intent.transition(IntentState.SUBMITTED)
