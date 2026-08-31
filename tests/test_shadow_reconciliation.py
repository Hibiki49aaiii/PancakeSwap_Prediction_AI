from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from pancake_prediction.replay import ReplaySnapshot, RoundRecord
from pancake_prediction.research_ledger import ResearchPredictionRecord, feature_digest
from pancake_prediction.shadow_ledger import ShadowLedgerStore
from pancake_prediction.shadow_reconciliation import reconcile_shadow_settlements


def _round(
    *,
    epoch: int = 10,
    label: str = "bull",
    bull: int = 1_000,
    bear: int = 1_000,
    reward_fields: bool = True,
) -> RoundRecord:
    total = bull + bear
    fee = total * 300 // 10_000
    if label == "bull":
        base = bull
        reward = total - fee
        treasury = fee
    elif label == "bear":
        base = bear
        reward = total - fee
        treasury = fee
    else:
        base = 0
        reward = 0
        treasury = total
    return RoundRecord(
        epoch=epoch,
        start_block=100,
        start_timestamp=1_000,
        lock_block=200,
        lock_timestamp=1_300,
        lock_round_id=1_000,
        lock_price=30_000_000_000,
        end_block=300,
        end_timestamp=1_600,
        close_round_id=2_000,
        close_price=(
            30_100_000_000
            if label == "bull"
            else 29_900_000_000
            if label == "bear"
            else 30_000_000_000
        ),
        bull_amount_wei=bull,
        bear_amount_wei=bear,
        total_amount_wei=total,
        bet_count=10,
        reward_base_cal_amount_wei=base if reward_fields else None,
        reward_amount_wei=reward if reward_fields else None,
        treasury_amount_wei=treasury if reward_fields else None,
        label=label,
        issues=(),
    )


def _prediction(
    *,
    epoch: int = 10,
    action: str = "bull",
) -> ResearchPredictionRecord:
    return ResearchPredictionRecord(
        market="BNBUSD",
        epoch=epoch,
        decision_timestamp_ms=1_280_000,
        model_id="shadow-test",
        feature_set_id="full-v1",
        raw_probability_ppm=620_000,
        calibrated_probability_ppm=600_000,
        expected_value_wei=10,
        action=action,
        feature_digest=feature_digest({"epoch": epoch}),
        train_max_epoch=epoch - 3,
        metadata={
            "stake_wei": 100,
            "bet_gas_wei": 2,
            "claim_gas_wei": 1,
            "treasury_fee_bps": 300,
        },
    )


def _store(tmp_path: Path) -> ShadowLedgerStore:
    store = ShadowLedgerStore(tmp_path / "shadow.sqlite3")
    store.initialize()
    return store


def _replay(record: RoundRecord) -> ReplaySnapshot:
    return ReplaySnapshot(
        format_version=1,
        market="BNBUSD",
        input_digest="a" * 64,
        rounds=(record,),
    )


def test_reconcile_winning_shadow_prediction_uses_final_parimutuel_pool(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.append_prediction(_prediction())

    report = reconcile_shadow_settlements(store, _replay(_round()))
    assert report.appended_settlement_count == 1
    assert report.existing_settlement_count == 0
    assert report.unresolved_count == 0

    audit = store.audit()
    assert audit.settlement_count == 1
    assert audit.observed_pnl_count == 1
    # Same counterfactual settlement semantics as the canonical backtest:
    # gross=185, so 185 - 100 stake - 2 bet gas - 1 claim gas = 82.
    assert audit.observed_pnl_wei == 82
    settlement = report.appended_events[0].payload["record"]
    assert isinstance(settlement, dict)
    assert settlement["outcome"] == "bull"
    assert settlement["settled_timestamp_ms"] == 1_600_000
    assert settlement["result_source_digest"]


def test_reconcile_loss_and_tie_charge_stake_and_bet_gas_only(
    tmp_path: Path,
) -> None:
    losing_store = ShadowLedgerStore(tmp_path / "loss.sqlite3")
    losing_store.initialize()
    losing_store.append_prediction(_prediction(action="bull"))
    reconcile_shadow_settlements(
        losing_store,
        _replay(_round(label="bear")),
    )
    assert losing_store.audit().observed_pnl_wei == -102

    tie_store = ShadowLedgerStore(tmp_path / "tie.sqlite3")
    tie_store.initialize()
    tie_store.append_prediction(_prediction(action="bull"))
    reconcile_shadow_settlements(
        tie_store,
        _replay(_round(label="tie")),
    )
    assert tie_store.audit().observed_pnl_wei == -102


def test_reconcile_skip_records_zero_pnl_but_not_actionable_pnl(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.append_prediction(_prediction(action="skip"))

    reconcile_shadow_settlements(store, _replay(_round(label="bear")))
    audit = store.audit()
    assert audit.settlement_count == 1
    assert audit.actionable_prediction_count == 0
    assert audit.observed_pnl_count == 0
    assert audit.observed_pnl_wei == 0


def test_reconcile_is_idempotent_and_reports_existing_settlement(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.append_prediction(_prediction())
    first = reconcile_shadow_settlements(store, _replay(_round()))
    second = reconcile_shadow_settlements(store, _replay(_round()))

    assert first.appended_settlement_count == 1
    assert second.appended_settlement_count == 0
    assert second.existing_settlement_count == 1
    assert store.audit().event_count == 2


def test_reconcile_leaves_unfinished_prediction_unresolved(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.append_prediction(_prediction())
    unfinished = replace(
        _round(),
        end_block=None,
        end_timestamp=None,
        close_round_id=None,
        close_price=None,
        label="incomplete",
        reward_base_cal_amount_wei=None,
        reward_amount_wei=None,
        treasury_amount_wei=None,
    )

    report = reconcile_shadow_settlements(store, _replay(unfinished))
    assert report.appended_settlement_count == 0
    assert report.unresolved_count == 1
    assert store.audit().settlement_count == 0


def test_reconcile_rejects_reward_integrity_mismatch_before_append(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.append_prediction(_prediction())
    corrupt = replace(_round(), treasury_amount_wei=999)

    with pytest.raises(ValueError, match="reward calculation"):
        reconcile_shadow_settlements(store, _replay(corrupt))
    assert store.audit().settlement_count == 0


def test_reconcile_rejects_missing_economic_metadata_for_actionable_prediction(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.append_prediction(replace(_prediction(), metadata={}))

    with pytest.raises(ValueError, match=r"missing economic metadata|treasury_fee_bps"):
        reconcile_shadow_settlements(store, _replay(_round()))
    assert store.audit().settlement_count == 0


def test_reconcile_result_digest_changes_when_canonical_result_changes(
    tmp_path: Path,
) -> None:
    first_store = ShadowLedgerStore(tmp_path / "first.sqlite3")
    first_store.initialize()
    first_store.append_prediction(_prediction())
    first = reconcile_shadow_settlements(first_store, _replay(_round()))
    first_record = first.appended_events[0].payload["record"]
    assert isinstance(first_record, dict)

    second_store = ShadowLedgerStore(tmp_path / "second.sqlite3")
    second_store.initialize()
    second_store.append_prediction(_prediction())
    second = reconcile_shadow_settlements(
        second_store,
        _replay(_round(bull=1_100, bear=900)),
    )
    second_record = second.appended_events[0].payload["record"]
    assert isinstance(second_record, dict)

    assert (
        first_record["result_source_digest"]
        != second_record["result_source_digest"]
    )
