import pytest

from pancake_prediction.research_ledger import (
    ResearchPredictionRecord,
    feature_digest,
    validate_research_prediction,
)


def _record() -> ResearchPredictionRecord:
    digest = feature_digest({"oracle_age_ms": 5000, "spot_oracle_gap_ppm": 1000})
    return ResearchPredictionRecord(
        market="BTCUSD",
        epoch=100,
        decision_timestamp_ms=123456789,
        model_id="wf-01",
        feature_set_id="cex-oracle-v1",
        raw_probability_ppm=620_000,
        calibrated_probability_ppm=600_000,
        expected_value_wei=1234,
        action="bull",
        feature_digest=digest,
        train_max_epoch=97,
        metadata={"fold": 1},
    )


def test_research_record_digest_is_deterministic() -> None:
    left = _record()
    right = _record()
    assert left.digest() == right.digest()
    assert len(left.digest()) == 64
    validate_research_prediction(left, purge_rounds=2)


def test_research_record_rejects_non_oos_training_boundary() -> None:
    record = _record()
    unsafe = ResearchPredictionRecord(**{**record.canonical_payload(), "train_max_epoch": 99})
    with pytest.raises(ValueError, match="not purged OOS"):
        validate_research_prediction(unsafe, purge_rounds=2)
