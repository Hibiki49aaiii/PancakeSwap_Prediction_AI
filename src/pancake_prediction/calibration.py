from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable

PPM = 1_000_000


@dataclass(frozen=True, slots=True)
class CalibrationPoint:
    probability_ppm: int
    outcome: int


@dataclass(frozen=True, slots=True)
class HistogramCalibrator:
    bins: int
    shrinkage: int
    global_probability_ppm: int
    bin_count: tuple[int, ...]
    bin_bull: tuple[int, ...]
    train_size: int
    train_max_epoch: int | None
    model_id: str

    def predict_ppm(self, probability_ppm: int) -> int:
        _validate_probability(probability_ppm)
        bucket = min(self.bins - 1, probability_ppm * self.bins // PPM)
        count = self.bin_count[bucket]
        bull = self.bin_bull[bucket]
        denominator = count + self.shrinkage
        if denominator == 0:
            return self.global_probability_ppm
        numerator = bull * PPM + self.shrinkage * self.global_probability_ppm
        return max(0, min(PPM, numerator // denominator))

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _validate_probability(probability_ppm: int) -> None:
    if probability_ppm < 0 or probability_ppm > PPM:
        raise ValueError("probability must be in [0, 1_000_000]")


def fit_histogram_calibrator(
    points: Iterable[CalibrationPoint],
    *,
    bins: int = 20,
    shrinkage: int = 50,
    train_max_epoch: int | None = None,
    model_id: str = "histogram-v1",
) -> HistogramCalibrator:
    if bins <= 1 or shrinkage < 0:
        raise ValueError("invalid calibration parameters")
    rows = list(points)
    if not rows:
        raise ValueError("at least one calibration point is required")
    counts = [0] * bins
    bulls = [0] * bins
    total_bull = 0
    for point in rows:
        _validate_probability(point.probability_ppm)
        if point.outcome not in (0, 1):
            raise ValueError("calibration outcome must be 0 or 1")
        bucket = min(bins - 1, point.probability_ppm * bins // PPM)
        counts[bucket] += 1
        bulls[bucket] += point.outcome
        total_bull += point.outcome
    global_probability_ppm = total_bull * PPM // len(rows)
    return HistogramCalibrator(
        bins=bins,
        shrinkage=shrinkage,
        global_probability_ppm=global_probability_ppm,
        bin_count=tuple(counts),
        bin_bull=tuple(bulls),
        train_size=len(rows),
        train_max_epoch=train_max_epoch,
        model_id=model_id,
    )
