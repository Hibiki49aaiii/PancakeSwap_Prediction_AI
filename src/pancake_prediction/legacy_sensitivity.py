from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace

from .clickhouse import ClickHouseParameterizedJsonSource
from .legacy_benchmark import LegacyEconomicBenchmarkConfig
from .legacy_campaign import (
    LegacySupportingCampaignConfig,
    LegacySupportingCampaignReport,
    run_legacy_supporting_campaign,
)
from .legacy_rounds import LegacyRoundAuditReport, LegacyRoundRecord


@dataclass(frozen=True, slots=True)
class LegacySensitivityScenarioResult:
    name: str
    evaluation_digest: str
    economic_config: LegacyEconomicBenchmarkConfig
    economic_summary: dict[str, object]

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "evaluation_digest": self.evaluation_digest,
            "economic_config": asdict(self.economic_config),
            "economic_summary": self.economic_summary,
        }


@dataclass(frozen=True, slots=True)
class LegacySupportingSensitivityReport:
    authoritative: bool
    campaign_digest: str
    scenarios: tuple[LegacySensitivityScenarioResult, ...]

    @property
    def positive_pnl_scenarios(self) -> int:
        count = 0
        for scenario in self.scenarios:
            value = scenario.economic_summary.get("pnl_wei")
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                count += 1
        return count

    @property
    def min_pnl_wei(self) -> int | None:
        values = [
            value
            for scenario in self.scenarios
            if isinstance((value := scenario.economic_summary.get("pnl_wei")), int)
            and not isinstance(value, bool)
        ]
        return min(values) if values else None

    @property
    def min_roi_ppm(self) -> int | None:
        values = [
            value
            for scenario in self.scenarios
            if isinstance((value := scenario.economic_summary.get("roi_ppm")), int)
            and not isinstance(value, bool)
        ]
        return min(values) if values else None

    @property
    def max_drawdown_wei(self) -> int | None:
        values = [
            value
            for scenario in self.scenarios
            if isinstance(
                (value := scenario.economic_summary.get("max_drawdown_wei")),
                int,
            )
            and not isinstance(value, bool)
        ]
        return max(values) if values else None

    def payload(self) -> dict[str, object]:
        return {
            "authoritative": self.authoritative,
            "profitability_gate_eligible": False,
            "campaign_digest": self.campaign_digest,
            "scenario_count": len(self.scenarios),
            "positive_pnl_scenarios": self.positive_pnl_scenarios,
            "all_scenarios_positive_pnl": (
                bool(self.scenarios)
                and self.positive_pnl_scenarios == len(self.scenarios)
            ),
            "min_pnl_wei": self.min_pnl_wei,
            "min_roi_ppm": self.min_roi_ppm,
            "max_drawdown_wei": self.max_drawdown_wei,
            "scenarios": [scenario.as_dict() for scenario in self.scenarios],
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


def _validate_scenarios(
    scenarios: Mapping[str, LegacyEconomicBenchmarkConfig],
) -> tuple[tuple[str, LegacyEconomicBenchmarkConfig], ...]:
    if len(scenarios) < 2:
        raise ValueError("at least two legacy sensitivity scenarios are required")
    if len(scenarios) > 20:
        raise ValueError("at most twenty legacy sensitivity scenarios are allowed")
    ordered: list[tuple[str, LegacyEconomicBenchmarkConfig]] = []
    for name, config in scenarios.items():
        if not name or name.strip() != name or len(name) > 80:
            raise ValueError("legacy sensitivity scenario name must be non-empty and trimmed")
        config.validate()
        ordered.append((name, config))
    return tuple(sorted(ordered, key=lambda item: item[0]))


def run_legacy_supporting_sensitivity(
    rounds: tuple[LegacyRoundRecord, ...],
    audit: LegacyRoundAuditReport,
    source: ClickHouseParameterizedJsonSource,
    base_config: LegacySupportingCampaignConfig,
    scenarios: Mapping[str, LegacyEconomicBenchmarkConfig],
) -> LegacySupportingSensitivityReport:
    base_config.validate()
    validated = _validate_scenarios(scenarios)
    first: LegacySupportingCampaignReport | None = None
    results: list[LegacySensitivityScenarioResult] = []
    for name, economic_config in validated:
        config = replace(base_config, economics=economic_config)
        config.validate()
        report = run_legacy_supporting_campaign(rounds, audit, source, config)
        if first is None:
            first = report
        else:
            if report.manifest.digest != first.manifest.digest:
                raise ValueError("legacy sensitivity scenarios changed the source campaign digest")
            if report.probability_metrics != first.probability_metrics:
                raise ValueError("legacy sensitivity scenarios changed OOS probability metrics")
        results.append(
            LegacySensitivityScenarioResult(
                name=name,
                evaluation_digest=report.evaluation_digest,
                economic_config=economic_config,
                economic_summary=report.economic_summary,
            )
        )
    if first is None:
        raise ValueError("legacy sensitivity produced no scenarios")
    return LegacySupportingSensitivityReport(
        authoritative=False,
        campaign_digest=first.manifest.digest,
        scenarios=tuple(results),
    )
