from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

from .backtest import PoolProjection
from .baseline import ResearchFeatureRow
from .legacy_benchmark import LegacyEconomicBenchmarkConfig, run_legacy_economic_benchmark
from .legacy_microstructure_model import (
    LEGACY_MICROSTRUCTURE_V2_FEATURE_NAMES,
    run_legacy_microstructure_model,
)
from .legacy_model import LEGACY_FEATURE_NAMES, legacy_oos_to_backtest_signals
from .legacy_rounds import LegacyRoundRecord


def _micro_names(
    *,
    venue: str | None = None,
    kind: str | None = None,
    seconds: int | None = None,
) -> tuple[str, ...]:
    names: list[str] = []
    venues = (venue,) if venue is not None else ("spot", "perp")
    kinds = (kind,) if kind is not None else ("return", "flow_imbalance", "trade_count")
    horizons = (seconds,) if seconds is not None else (5, 20, 60)
    for selected_venue in venues:
        for selected_kind in kinds:
            for horizon in horizons:
                suffix = "ppm" if selected_kind != "trade_count" else ""
                tail = f"_{suffix}" if suffix else ""
                names.append(f"{selected_venue}_{selected_kind}_{horizon}s{tail}")
    return tuple(names)


ABLATION_FEATURE_SETS: dict[str, tuple[str, ...]] = {
    "v2_all": LEGACY_MICROSTRUCTURE_V2_FEATURE_NAMES,
    "spot_micro": LEGACY_FEATURE_NAMES + _micro_names(venue="spot"),
    "perp_micro": LEGACY_FEATURE_NAMES + _micro_names(venue="perp"),
    "returns": LEGACY_FEATURE_NAMES + _micro_names(kind="return"),
    "flow": LEGACY_FEATURE_NAMES + _micro_names(kind="flow_imbalance"),
    "intensity": LEGACY_FEATURE_NAMES + _micro_names(kind="trade_count"),
    "horizon_5s": LEGACY_FEATURE_NAMES + _micro_names(seconds=5),
    "horizon_20s": LEGACY_FEATURE_NAMES + _micro_names(seconds=20),
    "horizon_60s": LEGACY_FEATURE_NAMES + _micro_names(seconds=60),
}


@dataclass(frozen=True, slots=True)
class MicrostructureAblationRow:
    name: str
    feature_names: tuple[str, ...]
    probability: dict[str, object]
    economics: dict[str, object]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class MicrostructureAblationReport:
    source_class: str
    authoritative: bool
    baseline_probability: dict[str, object]
    baseline_economics: dict[str, object]
    variants: tuple[MicrostructureAblationRow, ...]

    def payload(self) -> dict[str, object]:
        return {
            "source_class": self.source_class,
            "authoritative": self.authoritative,
            "baseline_probability": self.baseline_probability,
            "baseline_economics": self.baseline_economics,
            "variants": [row.as_dict() for row in self.variants],
            "exploratory_model_selection": True,
            "independent_confirmation_required": True,
            "profitability_gate_eligible": False,
            "scenario_only": True,
        }

    @property
    def ablation_digest(self) -> str:
        raw = (
            json.dumps(
                self.payload(),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )
            + "\n"
        ).encode()
        return hashlib.sha256(raw).hexdigest()

    def as_dict(self) -> dict[str, object]:
        payload = self.payload()
        payload["ablation_digest"] = self.ablation_digest
        return payload


def run_legacy_microstructure_ablation(
    rounds: tuple[LegacyRoundRecord, ...],
    rows: tuple[ResearchFeatureRow, ...],
    projections: dict[int, PoolProjection],
    economic_config: LegacyEconomicBenchmarkConfig,
    *,
    baseline_probability: dict[str, object],
    baseline_economics: dict[str, object],
    min_train_rounds: int,
    test_rounds: int,
    purge_rounds: int,
    embargo_rounds: int,
    calibration_rounds: int,
    calibration_bins: int = 10,
    calibration_shrinkage: int = 20,
) -> MicrostructureAblationReport:
    variants: list[MicrostructureAblationRow] = []
    for name, feature_names in ABLATION_FEATURE_SETS.items():
        model = run_legacy_microstructure_model(
            rounds,
            rows,
            feature_names=feature_names,
            feature_set_id=f"legacy-micro-ablation-{name}",
            fold_prefix=f"legacy-micro-ablation-{name}-wf",
            min_train_rounds=min_train_rounds,
            test_rounds=test_rounds,
            purge_rounds=purge_rounds,
            embargo_rounds=embargo_rounds,
            calibration_rounds=calibration_rounds,
            calibration_bins=calibration_bins,
            calibration_shrinkage=calibration_shrinkage,
        )
        economics = run_legacy_economic_benchmark(
            rounds,
            legacy_oos_to_backtest_signals(model.signals),
            projections,
            economic_config,
        )
        variants.append(
            MicrostructureAblationRow(
                name=name,
                feature_names=feature_names,
                probability=model.metrics.as_dict(),
                economics=economics.summary(),
            )
        )
    return MicrostructureAblationReport(
        source_class="third_party_historical_benchmark",
        authoritative=False,
        baseline_probability=baseline_probability,
        baseline_economics=baseline_economics,
        variants=tuple(variants),
    )
