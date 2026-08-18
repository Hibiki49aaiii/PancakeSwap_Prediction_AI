from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from pancake_prediction.execution_intent import ExecutionIntentStore, IntentState
from pancake_prediction.prediction_tx import (
    BetSide,
    build_prediction_bet_intent,
    encode_bet_calldata,
)
from pancake_prediction.stage5_evidence import (
    EvidenceOrigin,
    Stage5ForkEvidence,
    evaluate_stage5b_fork_gate,
    ledger_sha256,
)

SOURCE_SHA = "12" * 20
BLOCK_HASH = "0x" + "ab" * 32
BULL_SENDER = "0x" + "11" * 20
BEAR_SENDER = "0x" + "22" * 20
OTHER_SENDER = "0x" + "33" * 20
RESTART_SENDER = "0x" + "44" * 20
DROP_SENDER = "0x" + "55" * 20
REORG_SENDER = "0x" + "66" * 20
WRONG_TARGET = "0x" + "99" * 20
DROP_HASH = "0x" + "aa" * 32
DROP_REPLACEMENT_HASH = "0x" + "bb" * 32
REORG_HASH = "0x" + "cc" * 32
REORG_REPLACEMENT_HASH = "0x" + "dd" * 32


def _store(tmp_path: Path) -> ExecutionIntentStore:
    store = ExecutionIntentStore(tmp_path / "execution.sqlite3")
    store.initialize()
    return store


def _tx_hash(seed: int) -> str:
    return "0x" + f"{seed:064x}"


def _finalize_bet(
    store: ExecutionIntentStore,
    *,
    sender: str,
    epoch: int,
    side: BetSide,
) -> int:
    intent = build_prediction_bet_intent(
        store,
        market="BNBUSD",
        sender=sender,
        epoch=epoch,
        side=side,
        stake_wei=1,
    )
    store.reserve_nonce(intent.id, 0)
    store.begin_submission(intent.id)
    store.mark_submitted(intent.id, _tx_hash(epoch))
    store.set_reconciliation_state(intent.id, IntentState.FINALIZED)
    return intent.id


def _raw_intent(
    store: ExecutionIntentStore,
    *,
    key: str,
    sender: str,
) -> int:
    return store.get_or_create(
        idempotency_key=key,
        sender=sender,
        target=WRONG_TARGET,
        calldata="0x1234",
        value_wei=1,
    ).id


def _record_scenario_journal(store: ExecutionIntentStore) -> dict[str, int]:
    restart_id = _raw_intent(store, key="restart", sender=RESTART_SENDER)
    store.reserve_nonce(restart_id, 0)
    store.begin_submission(restart_id)
    store.recover_submitting(
        restart_id,
        state=IntentState.RETRYABLE,
        outcome="interrupted",
        error="submission was interrupted before an outcome was durably recorded",
    )
    store.set_reconciliation_state(restart_id, IntentState.FAILED, error="fixture done")
    store.record_observation(
        scenario="restart_recovery",
        observed=True,
        detail={
            "intent_id": restart_id,
            "nonce": 0,
            "attempt_outcome": "interrupted",
        },
    )

    drop_id = _raw_intent(store, key="drop", sender=DROP_SENDER)
    store.reserve_nonce(drop_id, 0)
    store.begin_submission(drop_id)
    store.mark_submitted(drop_id, DROP_HASH)
    store.set_reconciliation_state(
        drop_id,
        IntentState.RETRYABLE,
        error="reserved nonce is unconsumed; safe to retry the same nonce on local fork",
    )
    store.reserve_nonce(drop_id, 0)
    store.begin_submission(drop_id)
    store.mark_submitted(drop_id, DROP_REPLACEMENT_HASH)
    store.set_reconciliation_state(drop_id, IntentState.FINALIZED)
    store.record_observation(
        scenario="dropped_or_replaced_recovery",
        observed=True,
        detail={
            "intent_id": drop_id,
            "dropped_tx_hash": DROP_HASH,
            "reserved_nonce": 0,
            "replacement_tx_hash": DROP_REPLACEMENT_HASH,
        },
    )

    reorg_id = _raw_intent(store, key="reorg", sender=REORG_SENDER)
    store.reserve_nonce(reorg_id, 0)
    store.begin_submission(reorg_id)
    store.mark_submitted(reorg_id, REORG_HASH)
    store.set_reconciliation_state(reorg_id, IntentState.MINED)
    store.set_reconciliation_state(reorg_id, IntentState.REORGED, error="reorg")
    store.set_reconciliation_state(reorg_id, IntentState.RETRYABLE, error="nonce free")
    store.reserve_nonce(reorg_id, 0)
    store.begin_submission(reorg_id)
    store.mark_submitted(reorg_id, REORG_REPLACEMENT_HASH)
    store.set_reconciliation_state(reorg_id, IntentState.FINALIZED)
    store.record_observation(
        scenario="reorg_reconciliation",
        observed=True,
        detail={
            "intent_id": reorg_id,
            "snapshot_id": "0x1",
            "reorged_tx_hash": REORG_HASH,
            "reserved_nonce": 0,
            "replacement_tx_hash": REORG_REPLACEMENT_HASH,
        },
    )

    store.record_observation(
        scenario="non_loopback_rejection",
        observed=True,
        detail={"probe_url": "https://example.invalid"},
    )
    return {
        "restart_recovery": restart_id,
        "dropped_or_replaced_recovery": drop_id,
        "reorg_reconciliation": reorg_id,
    }


def _evidence(
    path: Path,
    *,
    origin: EvidenceOrigin = EvidenceOrigin.OBSERVED,
    **scenario_changes: bool,
) -> Stage5ForkEvidence:
    scenarios = {
        "restart_recovery": True,
        "dropped_or_replaced_recovery": True,
        "reorg_reconciliation": True,
        "non_loopback_rejection": True,
    }
    scenarios.update(scenario_changes)
    return Stage5ForkEvidence.create(
        origin=origin,
        source_sha=SOURCE_SHA,
        recorded_at="2026-08-19T01:50:00+09:00",
        campaign_id="test-campaign",
        market="BNBUSD",
        chain_id=56,
        fork_block_number=12_345_678,
        fork_block_hash=BLOCK_HASH,
        anvil_version="anvil 1.7.1",
        ledger_sha256=ledger_sha256(path),
        scenarios=scenarios,
    )


def _complete_campaign(store: ExecutionIntentStore) -> dict[str, int]:
    _finalize_bet(store, sender=BULL_SENDER, epoch=100, side=BetSide.BULL)
    _finalize_bet(store, sender=BEAR_SENDER, epoch=101, side=BetSide.BEAR)
    return _record_scenario_journal(store)


def test_complete_observed_campaign_is_ready(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _complete_campaign(store)
    result = evaluate_stage5b_fork_gate(
        ledger_path=store.path,
        evidence=_evidence(store.path),
        expected_source_sha=SOURCE_SHA,
    )
    assert result.ready is True
    assert result.blockers == ()
    assert result.finalized_bull == 1
    assert result.finalized_bear == 1


def test_json_claim_without_ledger_scenario_evidence_cannot_clear_gate(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _finalize_bet(store, sender=BULL_SENDER, epoch=100, side=BetSide.BULL)
    _finalize_bet(store, sender=BEAR_SENDER, epoch=101, side=BetSide.BEAR)

    result = evaluate_stage5b_fork_gate(
        ledger_path=store.path,
        evidence=_evidence(store.path),
    )
    assert result.ready is False
    assert "scenario_not_observed_in_ledger:restart_recovery" in result.blockers
    assert "scenario_detail_invalid:restart_recovery" in result.blockers


def test_scenario_observation_must_point_to_same_intent_history(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ids = _complete_campaign(store)
    wrong_intent_id = ids["dropped_or_replaced_recovery"]
    detail = json.dumps(
        {
            "intent_id": wrong_intent_id,
            "nonce": 0,
            "attempt_outcome": "interrupted",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    with sqlite3.connect(store.path) as conn:
        conn.execute(
            "UPDATE execution_observations SET detail_json=? WHERE scenario='restart_recovery'",
            (detail,),
        )
        conn.commit()

    result = evaluate_stage5b_fork_gate(
        ledger_path=store.path,
        evidence=_evidence(store.path),
    )
    assert result.ready is False
    assert (
        "scenario_transition_missing:restart_recovery:submitting->retryable"
        in result.blockers
    )
    assert "scenario_attempt_missing:restart_recovery:interrupted" in result.blockers


def test_empty_campaign_never_clears_gate(tmp_path: Path) -> None:
    store = _store(tmp_path)
    result = evaluate_stage5b_fork_gate(
        ledger_path=store.path,
        evidence=_evidence(store.path),
    )
    assert result.ready is False
    assert "empty_execution_campaign" in result.blockers
    assert "finalized_bull_missing" in result.blockers
    assert "finalized_bear_missing" in result.blockers


def test_final_state_without_submitted_attempt_cannot_fake_bull(tmp_path: Path) -> None:
    store = _store(tmp_path)
    fake = build_prediction_bet_intent(
        store,
        market="BNBUSD",
        sender=BULL_SENDER,
        epoch=100,
        side=BetSide.BULL,
        stake_wei=1,
    )
    store.set_reconciliation_state(fake.id, IntentState.FINALIZED)
    _finalize_bet(store, sender=BEAR_SENDER, epoch=101, side=BetSide.BEAR)

    result = evaluate_stage5b_fork_gate(
        ledger_path=store.path,
        evidence=_evidence(store.path),
    )
    assert "finalized_bull_missing" in result.blockers


def test_wrong_target_cannot_fake_finalized_bull(tmp_path: Path) -> None:
    store = _store(tmp_path)
    fake = store.get_or_create(
        idempotency_key="fake-bull",
        sender=OTHER_SENDER,
        target=WRONG_TARGET,
        calldata=encode_bet_calldata(BetSide.BULL, 100),
        value_wei=1,
    )
    store.reserve_nonce(fake.id, 0)
    store.begin_submission(fake.id)
    store.mark_submitted(fake.id, _tx_hash(999))
    store.set_reconciliation_state(fake.id, IntentState.FINALIZED)
    _finalize_bet(store, sender=BEAR_SENDER, epoch=101, side=BetSide.BEAR)

    result = evaluate_stage5b_fork_gate(
        ledger_path=store.path,
        evidence=_evidence(store.path),
    )
    assert "finalized_bull_missing" in result.blockers


def test_zero_value_cannot_fake_finalized_bull(tmp_path: Path) -> None:
    store = _store(tmp_path)
    fake = store.get_or_create(
        idempotency_key="zero-bull",
        sender=OTHER_SENDER,
        target="0x18B2A687610328590Bc8F2e5fEdDe3b582A49cdA",
        calldata=encode_bet_calldata(BetSide.BULL, 100),
        value_wei=0,
    )
    store.reserve_nonce(fake.id, 0)
    store.begin_submission(fake.id)
    store.mark_submitted(fake.id, _tx_hash(998))
    store.set_reconciliation_state(fake.id, IntentState.FINALIZED)
    _finalize_bet(store, sender=BEAR_SENDER, epoch=101, side=BetSide.BEAR)

    result = evaluate_stage5b_fork_gate(
        ledger_path=store.path,
        evidence=_evidence(store.path),
    )
    assert "finalized_bull_missing" in result.blockers


def test_unresolved_intent_blocks_even_with_bull_and_bear(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _complete_campaign(store)
    build_prediction_bet_intent(
        store,
        market="BNBUSD",
        sender=OTHER_SENDER,
        epoch=102,
        side=BetSide.BULL,
        stake_wei=1,
    )

    result = evaluate_stage5b_fork_gate(
        ledger_path=store.path,
        evidence=_evidence(store.path),
    )
    assert "unresolved_execution_intents" in result.blockers


def test_assumed_origin_cannot_clear_gate(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _complete_campaign(store)
    result = evaluate_stage5b_fork_gate(
        ledger_path=store.path,
        evidence=_evidence(store.path, origin=EvidenceOrigin.ASSUMED),
    )
    assert "evidence_not_observed" in result.blockers


def test_each_required_scenario_must_be_claimed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _complete_campaign(store)
    result = evaluate_stage5b_fork_gate(
        ledger_path=store.path,
        evidence=_evidence(store.path, reorg_reconciliation=False),
    )
    assert "scenario_not_claimed:reorg_reconciliation" in result.blockers


def test_ledger_mutation_invalidates_evidence(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _complete_campaign(store)
    evidence = _evidence(store.path)
    store.get_or_create(
        idempotency_key="mutation",
        sender=OTHER_SENDER,
        target=WRONG_TARGET,
        calldata="0x1234",
        value_wei=1,
    )

    result = evaluate_stage5b_fork_gate(
        ledger_path=store.path,
        evidence=evidence,
    )
    assert "ledger_hash_mismatch" in result.blockers


def test_claim_digest_binds_origin_and_claim_metadata(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _complete_campaign(store)
    payload = _evidence(store.path, origin=EvidenceOrigin.ASSUMED).as_dict()
    payload["origin"] = "observed"

    with pytest.raises(ValueError, match="claim_sha256"):
        Stage5ForkEvidence.from_json_bytes(json.dumps(payload).encode())


def test_claim_digest_binds_fork_block_hash_and_anvil_version(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _complete_campaign(store)
    payload = _evidence(store.path).as_dict()
    payload["fork_block_hash"] = "0x" + "cd" * 32

    with pytest.raises(ValueError, match="claim_sha256"):
        Stage5ForkEvidence.from_json_bytes(json.dumps(payload).encode())

    payload = _evidence(store.path).as_dict()
    payload["anvil_version"] = "anvil other-version"
    with pytest.raises(ValueError, match="claim_sha256"):
        Stage5ForkEvidence.from_json_bytes(json.dumps(payload).encode())


def test_non_boolean_scenario_is_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _complete_campaign(store)
    payload = _evidence(store.path).as_dict()
    raw_scenarios = payload["scenarios"]
    assert isinstance(raw_scenarios, dict)
    raw_scenarios["reorg_reconciliation"] = "false"

    with pytest.raises(ValueError, match="must be boolean"):
        Stage5ForkEvidence.from_json_bytes(json.dumps(payload).encode())


def test_source_sha_must_match_expected_branch_head(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _complete_campaign(store)
    result = evaluate_stage5b_fork_gate(
        ledger_path=store.path,
        evidence=_evidence(store.path),
        expected_source_sha="34" * 20,
    )
    assert "source_sha_mismatch" in result.blockers
