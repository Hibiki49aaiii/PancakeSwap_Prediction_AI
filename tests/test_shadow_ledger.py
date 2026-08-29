from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from pancake_prediction.research_ledger import ResearchPredictionRecord, feature_digest
from pancake_prediction.shadow_ledger import (
    ShadowLedgerStore,
    ShadowSettlementRecord,
    inspect_shadow_ledger_read_only,
)
from pancake_prediction.shadow_manifest import ShadowCampaignManifest


def _prediction(*, epoch: int = 100, action: str = "bull") -> ResearchPredictionRecord:
    return ResearchPredictionRecord(
        market="BNBUSD",
        epoch=epoch,
        decision_timestamp_ms=1_000_000 + epoch * 300_000,
        model_id="shadow-wf-v1",
        feature_set_id="full-v1",
        raw_probability_ppm=620_000,
        calibrated_probability_ppm=600_000,
        expected_value_wei=1234 if action != "skip" else None,
        action=action,
        feature_digest=feature_digest(
            {
                "epoch": epoch,
                "spot_return_ppm": 123,
                "oracle_age_ms": 5000,
            }
        ),
        train_max_epoch=epoch - 3,
        metadata={"fold": 1, "source": "test"},
    )


def _manifest(*, stake_wei: int = 100) -> ShadowCampaignManifest:
    return ShadowCampaignManifest(
        chain_id=56,
        market="BNBUSD",
        prediction_contract="0x" + "10" * 20,
        oracle_proxy_anchor="0x" + "11" * 20,
        chainlink_aggregator_anchor="0x" + "22" * 20,
        semantic_config={
            "inference": {
                "stake_wei": stake_wei,
                "purge_rounds": 2,
            },
            "campaign_policy": {"min_predictions": 1_000},
        },
    )


def _settlement(
    prediction: ResearchPredictionRecord,
    *,
    outcome: str = "bull",
    pnl: int | None = 100,
) -> ShadowSettlementRecord:
    return ShadowSettlementRecord(
        market=prediction.market,
        epoch=prediction.epoch,
        settled_timestamp_ms=prediction.decision_timestamp_ms + 300_000,
        outcome=outcome,
        result_source_digest=feature_digest(
            {"market": prediction.market, "epoch": prediction.epoch, "outcome": outcome}
        ),
        realized_pnl_wei=pnl,
        metadata={"source": "canonical-replay"},
    )


def _store(tmp_path: Path) -> ShadowLedgerStore:
    store = ShadowLedgerStore(tmp_path / "shadow.sqlite3")
    store.initialize()
    return store


def test_shadow_ledger_is_idempotent_and_hash_chained(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first_prediction = _prediction()
    first = store.append_prediction(first_prediction)
    retry = store.append_prediction(first_prediction)
    assert retry == first
    assert first.sequence == 1
    assert first.previous_digest == "0" * 64

    settlement = store.append_settlement(_settlement(first_prediction))
    settlement_retry = store.append_settlement(_settlement(first_prediction))
    assert settlement_retry == settlement
    assert settlement.sequence == 2
    assert settlement.previous_digest == first.event_digest

    report = store.audit()
    assert report.integrity_ready is True
    assert report.event_count == 2
    assert report.head_digest == settlement.event_digest
    assert report.prediction_count == 1
    assert report.settlement_count == 1
    assert report.unresolved_count == 0
    assert report.actionable_prediction_count == 1
    assert report.settled_actionable_count == 1
    assert report.probability_scored_count == 1
    assert report.brier_score == pytest.approx(0.16)
    assert report.directional_accuracy == 1.0
    assert report.observed_pnl_count == 1
    assert report.observed_pnl_wei == 100
    assert report.as_dict()["profitability_gate_eligible"] is False
    assert report.as_dict()["signing_enabled"] is False
    assert report.as_dict()["live_broadcast"] is False


def test_shadow_ledger_rejects_conflicting_duplicate_prediction(tmp_path: Path) -> None:
    store = _store(tmp_path)
    original = _prediction()
    store.append_prediction(original)

    changed = replace(original, calibrated_probability_ppm=610_000)
    with pytest.raises(ValueError, match="conflicting shadow prediction"):
        store.append_prediction(changed)


def test_shadow_ledger_requires_prediction_before_settlement(tmp_path: Path) -> None:
    store = _store(tmp_path)
    prediction = _prediction()
    with pytest.raises(ValueError, match="shadow prediction missing"):
        store.append_settlement(_settlement(prediction))


def test_shadow_ledger_rejects_non_oos_prediction(tmp_path: Path) -> None:
    store = _store(tmp_path)
    unsafe = replace(_prediction(), train_max_epoch=99)
    with pytest.raises(ValueError, match="not purged OOS"):
        store.append_prediction(unsafe, purge_rounds=2)


def test_shadow_ledger_rejects_early_or_mismatched_settlement(tmp_path: Path) -> None:
    store = _store(tmp_path)
    prediction = _prediction()
    store.append_prediction(prediction)

    early = replace(
        _settlement(prediction),
        settled_timestamp_ms=prediction.decision_timestamp_ms - 1,
    )
    with pytest.raises(ValueError, match="precedes prediction"):
        store.append_settlement(early)

    wrong_market = replace(_settlement(prediction), market="BTCUSD")
    with pytest.raises(ValueError, match="shadow prediction missing"):
        store.append_settlement(wrong_market)


def test_shadow_ledger_skip_cannot_receive_nonzero_pnl(tmp_path: Path) -> None:
    store = _store(tmp_path)
    prediction = _prediction(action="skip")
    store.append_prediction(prediction)

    with pytest.raises(ValueError, match="skip prediction cannot have non-zero"):
        store.append_settlement(_settlement(prediction, pnl=1))

    store.append_settlement(_settlement(prediction, pnl=0))
    report = store.audit()
    assert report.integrity_ready is True
    assert report.actionable_prediction_count == 0
    assert report.settled_actionable_count == 0
    assert report.observed_pnl_wei == 0


def test_shadow_audit_reports_probability_and_direction_across_rounds(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = _prediction(epoch=100, action="bull")
    second = replace(
        _prediction(epoch=101, action="bear"),
        raw_probability_ppm=400_000,
        calibrated_probability_ppm=400_000,
    )
    third = replace(
        _prediction(epoch=102, action="bull"),
        raw_probability_ppm=550_000,
        calibrated_probability_ppm=550_000,
    )

    for prediction in (first, second, third):
        store.append_prediction(prediction)

    store.append_settlement(_settlement(first, outcome="bull", pnl=100))
    store.append_settlement(_settlement(second, outcome="bear", pnl=80))
    store.append_settlement(_settlement(third, outcome="tie", pnl=None))

    report = store.audit()
    assert report.integrity_ready is True
    assert report.prediction_count == 3
    assert report.settlement_count == 3
    assert report.probability_scored_count == 2
    assert report.brier_score == pytest.approx(0.16)
    assert report.directional_accuracy == 1.0
    assert report.observed_pnl_count == 2
    assert report.observed_pnl_wei == 180
    assert report.decision_span_ms == (
        third.decision_timestamp_ms - first.decision_timestamp_ms
    )


def test_shadow_audit_detects_payload_tampering(tmp_path: Path) -> None:
    store = _store(tmp_path)
    prediction = _prediction()
    store.append_prediction(prediction)
    store.append_settlement(_settlement(prediction))

    connection = sqlite3.connect(store.path)
    try:
        connection.execute("DROP TRIGGER shadow_events_no_update")
        raw = connection.execute(
            "SELECT payload_json FROM shadow_ledger_events WHERE sequence = 1"
        ).fetchone()
        assert raw is not None
        payload = json.loads(str(raw[0]))
        payload["record"]["calibrated_probability_ppm"] = 990_000
        connection.execute(
            "UPDATE shadow_ledger_events SET payload_json = ? WHERE sequence = 1",
            (json.dumps(payload, sort_keys=True, separators=(",", ":")),),
        )
        connection.commit()
    finally:
        connection.close()

    report = store.audit()
    assert report.integrity_ready is False
    assert any("event digest mismatch" in item for item in report.integrity_errors)


def test_shadow_ledger_read_only_inspection_missing_path_does_not_create_file(
    tmp_path: Path,
) -> None:
    path = tmp_path / "missing.sqlite3"

    report = inspect_shadow_ledger_read_only(path)

    assert report.database_exists is False
    assert report.schema_ready is True
    assert report.binding_state == "absent"
    assert report.event_count is None
    assert path.exists() is False


def test_shadow_ledger_read_only_inspection_bound_manifest_preserves_main_file(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    binding = store.bind_campaign_manifest(_manifest())
    before = store.path.read_bytes()

    report = inspect_shadow_ledger_read_only(store.path)

    assert report.database_exists is True
    assert report.schema_ready is True
    assert report.binding_state == "bound"
    assert report.event_count == 0
    assert report.manifest_digest == binding.manifest_digest
    assert report.manifest_payload == binding.payload
    assert report.errors == ()
    assert store.path.read_bytes() == before


def test_shadow_ledger_read_only_inspection_rejects_non_ledger_sqlite(
    tmp_path: Path,
) -> None:
    path = tmp_path / "not-ledger.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE unrelated(value INTEGER)")

    report = inspect_shadow_ledger_read_only(path)

    assert report.database_exists is True
    assert report.schema_ready is False
    assert report.binding_state == "invalid"
    assert report.errors == ("shadow_ledger_core_schema_missing",)


def test_shadow_ledger_binds_manifest_idempotently(tmp_path: Path) -> None:
    store = _store(tmp_path)
    manifest = _manifest()

    first = store.bind_campaign_manifest(manifest)
    retry = store.bind_campaign_manifest(manifest)

    assert retry == first
    assert first.manifest_digest == manifest.digest
    loaded = store.campaign_manifest()
    assert loaded == first
    report = store.audit()
    assert report.campaign_manifest_digest == manifest.digest
    assert report.integrity_ready is True


def test_shadow_ledger_audit_rejects_purge_drift_from_manifest(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.bind_campaign_manifest(_manifest())
    store.append_prediction(_prediction())

    report = store.audit(purge_rounds=3)

    assert report.integrity_ready is False
    assert report.audit_purge_rounds == 3
    assert any(
        "purge_rounds differs from bound manifest" in item
        for item in report.integrity_errors
    )


def test_shadow_ledger_audit_rejects_prediction_market_drift_from_manifest(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.bind_campaign_manifest(_manifest())
    changed = replace(_prediction(), market="BTCUSD")
    store.append_prediction(changed)

    report = store.audit()

    assert report.integrity_ready is False
    assert report.campaign_manifest_market == "BNBUSD"
    assert any(
        "prediction market differs from bound campaign manifest" in item
        for item in report.integrity_errors
    )


def test_shadow_ledger_rejects_conflicting_campaign_manifest(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.bind_campaign_manifest(_manifest(stake_wei=100))

    with pytest.raises(ValueError, match="conflicts"):
        store.bind_campaign_manifest(_manifest(stake_wei=101))


def test_shadow_ledger_rejects_retroactive_manifest_binding(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.append_prediction(_prediction())

    with pytest.raises(ValueError, match="retroactively"):
        store.bind_campaign_manifest(_manifest())


def test_shadow_manifest_sqlite_triggers_block_update_and_delete(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.bind_campaign_manifest(_manifest())

    connection = sqlite3.connect(store.path)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE shadow_campaign_manifest SET manifest_digest = ? WHERE singleton = 1",
                ("f" * 64,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "DELETE FROM shadow_campaign_manifest WHERE singleton = 1"
            )
    finally:
        connection.close()


def test_shadow_ledger_sqlite_triggers_block_update_and_delete(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.append_prediction(_prediction())

    connection = sqlite3.connect(store.path)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "UPDATE shadow_ledger_events SET market = 'BTCUSD' WHERE sequence = 1"
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM shadow_ledger_events WHERE sequence = 1")
    finally:
        connection.close()
