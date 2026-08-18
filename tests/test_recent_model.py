from __future__ import annotations

from dataclasses import replace

from pancake_prediction.baseline import ResearchFeatureRow
from pancake_prediction.recent_model import (
    RECENT_CEX_FEATURE_NAMES,
    run_recent_canonical_cex_model,
)
from pancake_prediction.replay import ReplaySnapshot, RoundRecord
from pancake_prediction.walkforward import validate_oos_provenance


def _round(epoch: int) -> RoundRecord:
    bullish = epoch % 2 == 1
    start = 1_000 + epoch * 300
    lock = start + 300
    end = lock + 300
    lock_price = 30_000_000_000
    return RoundRecord(
        epoch=epoch,
        start_block=100 + epoch,
        start_timestamp=start,
        lock_block=200 + epoch,
        lock_timestamp=lock,
        lock_round_id=1_000 + epoch,
        lock_price=lock_price,
        end_block=300 + epoch,
        end_timestamp=end,
        close_round_id=2_000 + epoch,
        close_price=lock_price + (100_000_000 if bullish else -100_000_000),
        bull_amount_wei=10**18,
        bear_amount_wei=10**18,
        total_amount_wei=2 * 10**18,
        bet_count=10,
        reward_base_cal_amount_wei=10**18,
        reward_amount_wei=194 * 10**16,
        treasury_amount_wei=6 * 10**16,
        label="bull" if bullish else "bear",
        issues=(),
    )


def _row(record: RoundRecord) -> ResearchFeatureRow:
    bullish = record.label == "bull"
    sign = 1.0 if bullish else -1.0
    assert record.lock_timestamp is not None
    return ResearchFeatureRow(
        market="BNBUSD",
        epoch=record.epoch,
        decision_timestamp_ms=(record.lock_timestamp - 20) * 1_000,
        values={
            "spot_perp_basis_ppm": 100.0 * sign,
            "spot_flow_imbalance_ppm": 500_000.0 * sign,
            "perp_flow_imbalance_ppm": 250_000.0 * sign,
            "prior_bull_rate_20_ppm": 600_000.0 if bullish else 400_000.0,
            "prior_abs_return_12_ppm": 3_000.0 + record.epoch,
            "oracle_age_ms": 999_999_999.0,
            "spot_oracle_gap_ppm": 999_999_999.0,
            "pool_bull_share_ppm": 999_999_999.0,
        },
    )


def _fixture() -> tuple[ReplaySnapshot, tuple[ResearchFeatureRow, ...]]:
    rounds = tuple(_round(epoch) for epoch in range(1, 31))
    replay = ReplaySnapshot(
        format_version=1,
        market="BNBUSD",
        input_digest="a" * 64,
        rounds=rounds,
    )
    return replay, tuple(_row(record) for record in rounds)


def _run(replay: ReplaySnapshot, rows: tuple[ResearchFeatureRow, ...]):
    return run_recent_canonical_cex_model(
        replay,
        rows,
        min_train_rounds=10,
        test_rounds=5,
        purge_rounds=2,
        embargo_rounds=1,
        calibration_rounds=4,
        calibration_bins=4,
        calibration_shrinkage=4,
    )


def test_recent_model_uses_only_explicit_cex_history_features() -> None:
    replay, rows = _fixture()
    report = _run(replay, rows)

    assert report.feature_names == RECENT_CEX_FEATURE_NAMES
    assert report.feature_set_id == "recent-canonical-cex-history-v1"
    assert report.metrics.n_scored > 0
    validate_oos_provenance(report.signals.values(), purge_rounds=2)


def test_unavailable_chainlink_and_pool_features_cannot_change_recent_model() -> None:
    replay, rows = _fixture()
    original = _run(replay, rows)
    mutated_rows = tuple(
        replace(
            row,
            values={
                **row.values,
                "oracle_age_ms": -123_456_789.0,
                "spot_oracle_gap_ppm": -987_654_321.0,
                "pool_bull_share_ppm": float(row.epoch * 999_999),
                "pool_log_total_bnb": 1e12,
            },
        )
        for row in rows
    )
    mutated = _run(replay, mutated_rows)

    assert original.signals == mutated.signals
    assert original.metrics == mutated.metrics


def test_recent_model_excludes_ties_from_binary_scoring() -> None:
    replay, rows = _fixture()
    changed = list(replay.rounds)
    changed[20] = replace(
        changed[20],
        label="tie",
        close_price=changed[20].lock_price,
    )
    result = _run(replace(replay, rounds=tuple(changed)), rows)

    assert result.metrics.n_ties_excluded == 1
    assert changed[20].epoch not in result.signals
