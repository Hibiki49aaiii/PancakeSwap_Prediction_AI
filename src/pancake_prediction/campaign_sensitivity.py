from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, replace

from .baseline import ResearchFeatureRow
from .campaign_evaluation import (
    EconomicCampaignConfig,
    EconomicCampaignReport,
    run_source_bound_economic_campaign,
)
from .replay import ChainEvent, ReplaySnapshot

_ECONOMIC_FIELDS = frozenset(
    {
        "stake_wei",
        "bet_gas_wei",
        "claim_gas_wei",
        "inclusion_latency_seconds",
        "min_expected_value_wei",
    }
)
_SCENARIO_REQUIRED_FIELDS = frozenset(
    {
        "name",
        "stake_wei",
        "bet_gas_wei",
        "claim_gas_wei",
        "inclusion_latency_seconds",
    }
)
_SCENARIO_ALLOWED_FIELDS = _SCENARIO_REQUIRED_FIELDS | {"min_expected_value_wei"}


@dataclass(frozen=True, slots=True)
class EconomicSensitivityScenario:
    name: str
    config: EconomicCampaignConfig

    def validate(self) -> None:
        if not self.name or self.name.strip() != self.name:
            raise ValueError("scenario name must be non-empty and trimmed")
        if len(self.name) > 80:
            raise ValueError("scenario name must be at most 80 characters")
        self.config.validate()
        if self.config.run_ablation:
            raise ValueError("sensitivity scenarios must disable per-scenario ablation")


@dataclass(frozen=True, slots=True)
class EconomicSensitivityResult:
    name: str
    evaluation_digest: str
    config: EconomicCampaignConfig
    probability_metrics: dict[str, object]
    backtest_summary: dict[str, object]
    direction_signal_count: int
    pool_projection_count: int
    joint_epoch_count: int

    @classmethod
    def from_report(
        cls,
        name: str,
        report: EconomicCampaignReport,
    ) -> EconomicSensitivityResult:
        return cls(
            name=name,
            evaluation_digest=report.evaluation_digest,
            config=report.config,
            probability_metrics=report.probability_metrics,
            backtest_summary=report.backtest_summary,
            direction_signal_count=report.direction_signal_count,
            pool_projection_count=report.pool_projection_count,
            joint_epoch_count=report.joint_epoch_count,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "evaluation_digest": self.evaluation_digest,
            "config": asdict(self.config),
            "probability_metrics": self.probability_metrics,
            "backtest_summary": self.backtest_summary,
            "direction_signal_count": self.direction_signal_count,
            "pool_projection_count": self.pool_projection_count,
            "joint_epoch_count": self.joint_epoch_count,
        }


@dataclass(frozen=True, slots=True)
class EconomicSensitivityReport:
    campaign_digest: str
    scenarios: tuple[EconomicSensitivityResult, ...]

    @property
    def positive_pnl_scenarios(self) -> int:
        return sum(
            1
            for item in self.scenarios
            if isinstance(item.backtest_summary.get("pnl_wei"), int)
            and int(item.backtest_summary["pnl_wei"]) > 0
        )

    @property
    def min_pnl_wei(self) -> int | None:
        values = [
            int(item.backtest_summary["pnl_wei"])
            for item in self.scenarios
            if isinstance(item.backtest_summary.get("pnl_wei"), int)
        ]
        return min(values) if values else None

    @property
    def max_pnl_wei(self) -> int | None:
        values = [
            int(item.backtest_summary["pnl_wei"])
            for item in self.scenarios
            if isinstance(item.backtest_summary.get("pnl_wei"), int)
        ]
        return max(values) if values else None

    @property
    def min_roi_ppm(self) -> int | None:
        values = [
            int(item.backtest_summary["roi_ppm"])
            for item in self.scenarios
            if isinstance(item.backtest_summary.get("roi_ppm"), int)
        ]
        return min(values) if values else None

    @property
    def max_drawdown_wei(self) -> int | None:
        values = [
            int(item.backtest_summary["max_drawdown_wei"])
            for item in self.scenarios
            if isinstance(item.backtest_summary.get("max_drawdown_wei"), int)
        ]
        return max(values) if values else None

    def payload(self) -> dict[str, object]:
        return {
            "campaign_digest": self.campaign_digest,
            "scenario_count": len(self.scenarios),
            "positive_pnl_scenarios": self.positive_pnl_scenarios,
            "all_scenarios_positive_pnl": (
                bool(self.scenarios)
                and self.positive_pnl_scenarios == len(self.scenarios)
            ),
            "min_pnl_wei": self.min_pnl_wei,
            "max_pnl_wei": self.max_pnl_wei,
            "min_roi_ppm": self.min_roi_ppm,
            "max_drawdown_wei": self.max_drawdown_wei,
            "scenarios": [item.as_dict() for item in self.scenarios],
        }

    @property
    def sensitivity_digest(self) -> str:
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
        payload["sensitivity_digest"] = self.sensitivity_digest
        return payload


def _strict_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"sensitivity field {field} must be an integer")
    return value


def parse_sensitivity_scenarios(
    payload: object,
    *,
    base_config: EconomicCampaignConfig,
) -> tuple[EconomicSensitivityScenario, ...]:
    if not isinstance(payload, dict):
        raise ValueError("sensitivity file root must be an object")
    raw_scenarios = payload.get("scenarios")
    if not isinstance(raw_scenarios, list):
        raise ValueError("sensitivity file must contain a scenarios array")
    scenarios: list[EconomicSensitivityScenario] = []
    for index, raw in enumerate(raw_scenarios):
        if not isinstance(raw, dict):
            raise ValueError(f"sensitivity scenario {index} must be an object")
        keys = {str(key) for key in raw}
        missing = sorted(_SCENARIO_REQUIRED_FIELDS - keys)
        unknown = sorted(keys - _SCENARIO_ALLOWED_FIELDS)
        if missing:
            raise ValueError(
                f"sensitivity scenario {index} missing fields: {', '.join(missing)}"
            )
        if unknown:
            raise ValueError(
                f"sensitivity scenario {index} has unknown fields: {', '.join(unknown)}"
            )
        name = raw.get("name")
        if not isinstance(name, str):
            raise ValueError(f"sensitivity scenario {index} name must be a string")
        scenario_config = replace(
            base_config,
            stake_wei=_strict_int(raw.get("stake_wei"), field="stake_wei"),
            bet_gas_wei=_strict_int(raw.get("bet_gas_wei"), field="bet_gas_wei"),
            claim_gas_wei=_strict_int(raw.get("claim_gas_wei"), field="claim_gas_wei"),
            inclusion_latency_seconds=_strict_int(
                raw.get("inclusion_latency_seconds"),
                field="inclusion_latency_seconds",
            ),
            min_expected_value_wei=_strict_int(
                raw.get("min_expected_value_wei", 0),
                field="min_expected_value_wei",
            ),
            run_ablation=False,
        )
        scenarios.append(EconomicSensitivityScenario(name=name, config=scenario_config))
    return _validate_scenarios(scenarios)


def _structural_config(config: EconomicCampaignConfig) -> dict[str, object]:
    payload = asdict(config)
    for field in _ECONOMIC_FIELDS:
        payload.pop(field)
    return payload


def _validate_scenarios(
    scenarios: Iterable[EconomicSensitivityScenario],
) -> tuple[EconomicSensitivityScenario, ...]:
    ordered = tuple(scenarios)
    if len(ordered) < 2:
        raise ValueError("at least two sensitivity scenarios are required")
    if len(ordered) > 100:
        raise ValueError("at most 100 sensitivity scenarios are allowed")
    names: set[str] = set()
    structural: Mapping[str, object] | None = None
    for scenario in ordered:
        scenario.validate()
        if scenario.name in names:
            raise ValueError(f"duplicate sensitivity scenario name: {scenario.name}")
        names.add(scenario.name)
        current = _structural_config(scenario.config)
        if structural is None:
            structural = current
        elif current != structural:
            raise ValueError(
                "sensitivity scenarios may vary only stake/gas/latency/EV threshold"
            )
    return tuple(sorted(ordered, key=lambda item: item.name))


def run_source_bound_economic_sensitivity(
    replay: ReplaySnapshot,
    events: tuple[ChainEvent, ...],
    rows: Iterable[ResearchFeatureRow],
    *,
    campaign_digest: str,
    scenarios: Iterable[EconomicSensitivityScenario],
    feature_set_id: str = "full-v1",
) -> EconomicSensitivityReport:
    validated = _validate_scenarios(scenarios)
    cached_rows = tuple(rows)
    results: list[EconomicSensitivityResult] = []
    for scenario in validated:
        report = run_source_bound_economic_campaign(
            replay,
            events,
            cached_rows,
            campaign_digest=campaign_digest,
            config=scenario.config,
            feature_set_id=feature_set_id,
        )
        results.append(EconomicSensitivityResult.from_report(scenario.name, report))
    return EconomicSensitivityReport(
        campaign_digest=campaign_digest.lower(),
        scenarios=tuple(results),
    )
