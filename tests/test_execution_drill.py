from __future__ import annotations

import json
from dataclasses import replace

import pytest

from pancake_prediction_ai.evidence_gate import Evidence, EvidenceKind, EvidenceOrigin
from pancake_prediction_ai.execution_drill import (
    make_stage5a_evidence,
    run_stage5a_execution_drill,
    write_stage5a_evidence,
)


def test_stage5a_drill_exercises_restart_nonce_unknown_finalization_and_cleanup(tmp_path) -> None:
    database = tmp_path / "stage5a.sqlite3"
    result = run_stage5a_execution_drill(database, required_confirmations=3)

    assert result.passed
    assert result.journal_mode_wal
    assert result.synchronous_full
    assert result.unresolved_recovered_after_restart
    assert result.duplicate_active_nonce_rejected
    assert result.unknown_state_persisted_after_missing_receipt
    assert result.finalized_state_persisted_after_confirmations
    assert result.terminal_nonce_released
    assert result.terminal_reuse_cleanup_persisted
    assert result.unresolved_count_final == 0

    evidence = make_stage5a_evidence(
        result,
        recorded_at="2026-08-19T22:35:00+09:00",
    )
    assert evidence.kind is EvidenceKind.STAGE5A_DRILL
    assert evidence.origin is EvidenceOrigin.OBSERVED
    assert evidence.passed
    assert evidence.payload["schema"] == "stage5a_execution_drill_v1"
    assert evidence.payload["blockchain_transaction_created"] is False
    assert evidence.payload["transaction_signed"] is False
    assert evidence.payload["transaction_broadcast"] is False

    path = write_stage5a_evidence(evidence, tmp_path / "stage5a-evidence.json")
    loaded = Evidence.from_path(path)
    assert loaded == evidence


def test_stage5a_drill_refuses_existing_database_path(tmp_path) -> None:
    database = tmp_path / "existing.sqlite3"
    database.write_bytes(b"do-not-overwrite")
    with pytest.raises(ValueError, match="must not already exist"):
        run_stage5a_execution_drill(database)
    assert database.read_bytes() == b"do-not-overwrite"


def test_stage5a_drill_requires_positive_confirmation_threshold(tmp_path) -> None:
    with pytest.raises(ValueError, match="required_confirmations"):
        run_stage5a_execution_drill(tmp_path / "invalid.sqlite3", required_confirmations=0)


def test_stage5a_writer_rejects_payload_tampering(tmp_path) -> None:
    result = run_stage5a_execution_drill(tmp_path / "stage5a.sqlite3")
    evidence = make_stage5a_evidence(result)
    tampered_payload = dict(evidence.payload)
    tampered_payload["unresolved_count_final"] = 1
    tampered = replace(evidence, payload=tampered_payload)

    with pytest.raises(ValueError, match="SHA-256"):
        write_stage5a_evidence(tampered, tmp_path / "tampered.json")


def test_written_stage5a_document_is_canonical_json_object(tmp_path) -> None:
    result = run_stage5a_execution_drill(tmp_path / "stage5a.sqlite3")
    evidence = make_stage5a_evidence(result)
    path = write_stage5a_evidence(evidence, tmp_path / "stage5a.json")
    obj = json.loads(path.read_text())
    assert obj["kind"] == "stage5a_drill"
    assert obj["origin"] == "observed"
    assert obj["passed"] is True
    assert obj["artifact_sha256"] == evidence.artifact_sha256
