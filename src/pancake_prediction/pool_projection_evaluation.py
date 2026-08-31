from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import median

from .backtest import PoolProjection
from .economics import PPM
from .pool_projection_dataset import PoolProjectionDataset


@dataclass(frozen=True, slots=True)
class PoolProjectionAccuracyReport:
    dataset_digest: str
    row_count: int
    scored_rounds: int
    missing_projection_rounds: int
    mean_abs_bull_share_error_ppm: int | None
    median_abs_bull_share_error_ppm: int | None
    mean_abs_total_pool_error_ppm: int | None
    median_abs_total_pool_error_ppm: int | None
    mean_signed_total_pool_error_ppm: int | None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _bull_share_ppm(bull_wei: int, bear_wei: int) -> int | None:
    total = bull_wei + bear_wei
    if total <= 0:
        return None
    return bull_wei * PPM // total


def evaluate_pool_projection_accuracy(
    dataset: PoolProjectionDataset,
    projections: dict[int, PoolProjection],
) -> PoolProjectionAccuracyReport:
    bull_share_errors: list[int] = []
    total_abs_errors: list[int] = []
    total_signed_errors: list[int] = []
    missing = 0

    for row in dataset.rows:
        features = row.features
        projection = projections.get(features.epoch)
        if projection is None:
            missing += 1
            continue
        projection.validate()
        if projection.epoch != features.epoch:
            raise ValueError("pool projection epoch mismatch")
        if projection.generated_at > features.decision_timestamp:
            raise ValueError("pool projection was generated after decision cutoff")
        if projection.projected_bull_wei < features.observed_bull_wei:
            raise ValueError("projected bull pool is below observed decision pool")
        if projection.projected_bear_wei < features.observed_bear_wei:
            raise ValueError("projected bear pool is below observed decision pool")

        final_total = row.label.final_bull_wei + row.label.final_bear_wei
        projected_total = projection.projected_bull_wei + projection.projected_bear_wei
        if final_total <= 0:
            continue
        final_share = _bull_share_ppm(row.label.final_bull_wei, row.label.final_bear_wei)
        projected_share = _bull_share_ppm(
            projection.projected_bull_wei,
            projection.projected_bear_wei,
        )
        if final_share is not None and projected_share is not None:
            bull_share_errors.append(abs(projected_share - final_share))
        signed_total_error = (projected_total - final_total) * PPM // final_total
        total_signed_errors.append(signed_total_error)
        total_abs_errors.append(abs(signed_total_error))

    scored = len(total_abs_errors)
    return PoolProjectionAccuracyReport(
        dataset_digest=dataset.dataset_digest,
        row_count=len(dataset.rows),
        scored_rounds=scored,
        missing_projection_rounds=missing,
        mean_abs_bull_share_error_ppm=(
            None if not bull_share_errors else sum(bull_share_errors) // len(bull_share_errors)
        ),
        median_abs_bull_share_error_ppm=(
            None if not bull_share_errors else int(median(bull_share_errors))
        ),
        mean_abs_total_pool_error_ppm=(
            None if not total_abs_errors else sum(total_abs_errors) // len(total_abs_errors)
        ),
        median_abs_total_pool_error_ppm=(
            None if not total_abs_errors else int(median(total_abs_errors))
        ),
        mean_signed_total_pool_error_ppm=(
            None
            if not total_signed_errors
            else sum(total_signed_errors) // len(total_signed_errors)
        ),
    )
