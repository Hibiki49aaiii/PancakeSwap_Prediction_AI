from __future__ import annotations

import json
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
WRONG_TARGET = "0x" + "99" * 20


def _store(tmp_path: Path) -> ExecutionIntentStore:
    store = ExecutionIntentStore(tmp_path / "execution.sqlite3")
    store.initialize()
    return store


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
    store.set_reconciliation_state(intent.id, IntentState.FINALIZED)
    return intent.id


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


def _complete_campaign(store: ExecutionIntentStore) -> None:
    _finalize_bet(store, sender=BULL_SENDER, epoch=100, side=BetSide.BULL)
    _finalize_bet(store, sender=BEAR_SENDER, epoch=101, side=BetSide.BEAR)


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


def test_wrong_target_cannot_fake_finalized_bull(tmp_path: Path) -> None:
    store = _store(tmp_path)
    fake = store.get_or_create(
        idempotency_key="fake-bull",
        sender=OTHER_SENDER,
        target=WRONG_TARGET,
        calldata=encode_bet_calldata(BetSide.BULL, 100),
        value_wei=1,
    )
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


def test_each_required_scenario_must_be_observed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _complete_campaign(store)
    result = evaluate_stage5b_fork_gate(
        ledger_path=store.path,
        evidence=_evidence(store.path, reorg_reconciliation=False),
    )
    assert "scenario_not_observed:reorg_reconciliation" in result.blockers


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