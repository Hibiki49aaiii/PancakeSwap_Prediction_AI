from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class EconomicVariantSnapshot:
    feature_set_id: str
    removed_family: str | None
    n_scored: int
    brier_score: float
    brier_skill_score: float
    log_loss: float
    ece_10: float
    accuracy: float
    trade_count: int
    pnl_wei: int
    roi_ppm: int | None
    max_drawdown_wei: int

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class EconomicWindowSnapshot:
    label: str
    start_timestamp: int
    end_timestamp: int
    research_feature_rows: int
    variants: tuple[EconomicVariantSnapshot, ...]

    @property
    def duration_seconds(self) -> int:
        return self.end_timestamp - self.start_timestamp

    @property
    def full_variant(self) -> EconomicVariantSnapshot:
        matches = [item for item in self.variants if item.feature_set_id == "full-v1"]
        if len(matches) != 1:
            raise ValueError(f"{self.label} evidence must contain exactly one full-v1 variant")
        return matches[0]

    def as_dict(self) -> dict[str, object]:
        return {
            "label": self.label,
            "start_timestamp": self.start_timestamp,
            "end_timestamp": self.end_timestamp,
            "duration_seconds": self.duration_seconds,
            "research_feature_rows": self.research_feature_rows,
            "full_v1": self.full_variant.as_dict(),
            "variants": [item.as_dict() for item in self.variants],
        }


@dataclass(frozen=True, slots=True)
class EconomicWindowComparison:
    short_window: EconomicWindowSnapshot
    expanded_window: EconomicWindowSnapshot
    full_v1_delta: dict[str, int | float | None]
    feature_stability: dict[str, object]
    interpretation_flags: dict[str, bool]

    def payload(self) -> dict[str, object]:
        return {
            "comparison_version": 1,
            "short_window": self.short_window.as_dict(),
            "expanded_window": self.expanded_window.as_dict(),
            "full_v1_delta_expanded_minus_short": self.full_v1_delta,
            "feature_stability": self.feature_stability,
            "interpretation_flags": self.interpretation_flags,
            "profitability_gate_eligible": False,
            "full_historical_gate_satisfied": False,
            "signing_enabled": False,
            "live_broadcast": False,
        }

    @property
    def comparison_digest(self) -> str:
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
        payload["comparison_digest"] = self.comparison_digest
        return payload


def _mapping(value: object, *, field: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return value


def _sequence(value: object, *, field: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    return value


def _strict_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return value


def _optional_int(value: object, *, field: str) -> int | None:
    if value is None:
        return None
    return _strict_int(value, field=field)


def _strict_float(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    result = float(value)
    if result != result or result in (float("inf"), float("-inf")):
        raise ValueError(f"{field} must be finite")
    return result


def _strict_string(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _validate_evidence_boundary(evidence: Mapping[str, object], *, label: str) -> None:
    if evidence.get("success") is not True:
        raise ValueError(f"{label} evidence is not successful")
    semantic = _mapping(evidence.get("semantic_gate"), field=f"{label}.semantic_gate")
    if semantic.get("ready") is not True:
        raise ValueError(f"{label} semantic gate is not ready")
    if evidence.get("profitability_gate_eligible") is not False:
        raise ValueError(f"{label} profitability gate boundary is not fail-closed")
    if evidence.get("full_historical_gate_satisfied") is not False:
        raise ValueError(f"{label} historical gate must remain unsatisfied")
    if evidence.get("signing_enabled") is not False:
        raise ValueError(f"{label} signing must remain disabled")
    if evidence.get("live_broadcast") is not False:
        raise ValueError(f"{label} live broadcast must remain disabled")


def _parse_variant(value: object, *, label: str, index: int) -> EconomicVariantSnapshot:
    item = _mapping(value, field=f"{label}.ablation[{index}]")
    removed = item.get("removed_family")
    if removed is not None and not isinstance(removed, str):
        raise ValueError(f"{label}.ablation[{index}].removed_family must be string or null")
    return EconomicVariantSnapshot(
        feature_set_id=_strict_string(
            item.get("feature_set_id"),
            field=f"{label}.ablation[{index}].feature_set_id",
        ),
        removed_family=removed,
        n_scored=_strict_int(
            item.get("n_scored"),
            field=f"{label}.ablation[{index}].n_scored",
        ),
        brier_score=_strict_float(
            item.get("brier_score"),
            field=f"{label}.ablation[{index}].brier_score",
        ),
        brier_skill_score=_strict_float(
            item.get("brier_skill_score"),
            field=f"{label}.ablation[{index}].brier_skill_score",
        ),
        log_loss=_strict_float(
            item.get("log_loss"),
            field=f"{label}.ablation[{index}].log_loss",
        ),
        ece_10=_strict_float(
            item.get("ece_10"),
            field=f"{label}.ablation[{index}].ece_10",
        ),
        accuracy=_strict_float(
            item.get("accuracy"),
            field=f"{label}.ablation[{index}].accuracy",
        ),
        trade_count=_strict_int(
            item.get("trade_count"),
            field=f"{label}.ablation[{index}].trade_count",
        ),
        pnl_wei=_strict_int(
            item.get("pnl_wei"),
            field=f"{label}.ablation[{index}].pnl_wei",
        ),
        roi_ppm=_optional_int(
            item.get("roi_ppm"),
            field=f"{label}.ablation[{index}].roi_ppm",
        ),
        max_drawdown_wei=_strict_int(
            item.get("max_drawdown_wei"),
            field=f"{label}.ablation[{index}].max_drawdown_wei",
        ),
    )


def snapshot_from_robustness_evidence(
    evidence: Mapping[str, object],
    *,
    label: str,
) -> EconomicWindowSnapshot:
    _validate_evidence_boundary(evidence, label=label)
    source_window = _mapping(evidence.get("source_window"), field=f"{label}.source_window")
    start_timestamp = _strict_int(
        source_window.get("start_timestamp"),
        field=f"{label}.source_window.start_timestamp",
    )
    end_timestamp = _strict_int(
        source_window.get("end_timestamp"),
        field=f"{label}.source_window.end_timestamp",
    )
    if end_timestamp <= start_timestamp:
        raise ValueError(f"{label} source window must have positive duration")

    semantic = _mapping(evidence.get("semantic_gate"), field=f"{label}.semantic_gate")
    research_feature_rows = _strict_int(
        semantic.get("research_feature_rows"),
        field=f"{label}.semantic_gate.research_feature_rows",
    )

    campaign = _mapping(
        evidence.get("ablation_campaign"),
        field=f"{label}.ablation_campaign",
    )
    evaluation = _mapping(
        campaign.get("evaluation"),
        field=f"{label}.ablation_campaign.evaluation",
    )
    raw_variants = _sequence(
        evaluation.get("ablation"),
        field=f"{label}.ablation_campaign.evaluation.ablation",
    )
    variants = tuple(
        _parse_variant(item, label=label, index=index)
        for index, item in enumerate(raw_variants)
    )
    if len(variants) < 2:
        raise ValueError(f"{label} must contain full-v1 and at least one ablation")
    ids = [item.feature_set_id for item in variants]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{label} contains duplicate feature_set_id values")

    snapshot = EconomicWindowSnapshot(
        label=label,
        start_timestamp=start_timestamp,
        end_timestamp=end_timestamp,
        research_feature_rows=research_feature_rows,
        variants=variants,
    )
    _ = snapshot.full_variant
    return snapshot


def _rank(
    variants: tuple[EconomicVariantSnapshot, ...],
    *,
    metric: str,
    higher_is_better: bool,
) -> dict[str, int]:
    def value(item: EconomicVariantSnapshot) -> float:
        raw = getattr(item, metric)
        if not isinstance(raw, (int, float)) or isinstance(raw, bool):
            raise TypeError(f"unsupported ranking metric: {metric}")
        return float(raw)

    ordered = sorted(
        variants,
        key=lambda item: ((-value(item)) if higher_is_better else value(item), item.feature_set_id),
    )
    return {item.feature_set_id: index + 1 for index, item in enumerate(ordered)}


def _feature_stability(
    short: EconomicWindowSnapshot,
    expanded: EconomicWindowSnapshot,
) -> dict[str, object]:
    short_by_id = {item.feature_set_id: item for item in short.variants}
    expanded_by_id = {item.feature_set_id: item for item in expanded.variants}
    common = sorted(set(short_by_id).intersection(expanded_by_id))
    if len(common) < 2:
        raise ValueError("economic windows do not share enough feature variants")

    short_common = tuple(short_by_id[item] for item in common)
    expanded_common = tuple(expanded_by_id[item] for item in common)
    short_brier_rank = _rank(short_common, metric="brier_score", higher_is_better=False)
    expanded_brier_rank = _rank(expanded_common, metric="brier_score", higher_is_better=False)
    short_pnl_rank = _rank(short_common, metric="pnl_wei", higher_is_better=True)
    expanded_pnl_rank = _rank(expanded_common, metric="pnl_wei", higher_is_better=True)

    short_best_brier = min(short_brier_rank, key=short_brier_rank.__getitem__)
    expanded_best_brier = min(expanded_brier_rank, key=expanded_brier_rank.__getitem__)
    short_best_pnl = min(short_pnl_rank, key=short_pnl_rank.__getitem__)
    expanded_best_pnl = min(expanded_pnl_rank, key=expanded_pnl_rank.__getitem__)

    rank_changes = []
    for feature_set_id in common:
        short_item = short_by_id[feature_set_id]
        expanded_item = expanded_by_id[feature_set_id]
        rank_changes.append(
            {
                "feature_set_id": feature_set_id,
                "removed_family": short_item.removed_family,
                "short_brier_rank": short_brier_rank[feature_set_id],
                "expanded_brier_rank": expanded_brier_rank[feature_set_id],
                "brier_rank_change": (
                    expanded_brier_rank[feature_set_id] - short_brier_rank[feature_set_id]
                ),
                "short_pnl_rank": short_pnl_rank[feature_set_id],
                "expanded_pnl_rank": expanded_pnl_rank[feature_set_id],
                "pnl_rank_change": (
                    expanded_pnl_rank[feature_set_id] - short_pnl_rank[feature_set_id]
                ),
                "brier_score_delta": expanded_item.brier_score - short_item.brier_score,
                "brier_skill_delta": (
                    expanded_item.brier_skill_score - short_item.brier_skill_score
                ),
                "pnl_wei_delta": expanded_item.pnl_wei - short_item.pnl_wei,
                "roi_ppm_delta": (
                    None
                    if short_item.roi_ppm is None or expanded_item.roi_ppm is None
                    else expanded_item.roi_ppm - short_item.roi_ppm
                ),
            }
        )

    return {
        "common_feature_sets": common,
        "common_feature_set_count": len(common),
        "short_best_brier_feature_set": short_best_brier,
        "expanded_best_brier_feature_set": expanded_best_brier,
        "same_best_brier_feature_set": short_best_brier == expanded_best_brier,
        "short_best_pnl_feature_set": short_best_pnl,
        "expanded_best_pnl_feature_set": expanded_best_pnl,
        "same_best_pnl_feature_set": short_best_pnl == expanded_best_pnl,
        "brier_ranking_stable": short_brier_rank == expanded_brier_rank,
        "pnl_ranking_stable": short_pnl_rank == expanded_pnl_rank,
        "rank_changes": rank_changes,
    }


def compare_economic_windows(
    short_evidence: Mapping[str, object],
    expanded_evidence: Mapping[str, object],
    *,
    short_label: str = "one_day",
    expanded_label: str = "three_day",
) -> EconomicWindowComparison:
    short = snapshot_from_robustness_evidence(short_evidence, label=short_label)
    expanded = snapshot_from_robustness_evidence(expanded_evidence, label=expanded_label)
    if expanded.duration_seconds <= short.duration_seconds:
        raise ValueError("expanded evidence window must be longer than short evidence window")

    short_full = short.full_variant
    expanded_full = expanded.full_variant
    roi_delta = (
        None
        if short_full.roi_ppm is None or expanded_full.roi_ppm is None
        else expanded_full.roi_ppm - short_full.roi_ppm
    )
    delta: dict[str, int | float | None] = {
        "n_scored": expanded_full.n_scored - short_full.n_scored,
        "brier_score": expanded_full.brier_score - short_full.brier_score,
        "brier_skill_score": expanded_full.brier_skill_score - short_full.brier_skill_score,
        "log_loss": expanded_full.log_loss - short_full.log_loss,
        "ece_10": expanded_full.ece_10 - short_full.ece_10,
        "accuracy": expanded_full.accuracy - short_full.accuracy,
        "trade_count": expanded_full.trade_count - short_full.trade_count,
        "pnl_wei": expanded_full.pnl_wei - short_full.pnl_wei,
        "roi_ppm": roi_delta,
        "max_drawdown_wei": (
            expanded_full.max_drawdown_wei - short_full.max_drawdown_wei
        ),
        "research_feature_rows": (
            expanded.research_feature_rows - short.research_feature_rows
        ),
    }
    stability = _feature_stability(short, expanded)
    flags = {
        "expanded_brier_score_improved": expanded_full.brier_score < short_full.brier_score,
        "expanded_brier_skill_improved": (
            expanded_full.brier_skill_score > short_full.brier_skill_score
        ),
        "expanded_brier_skill_positive": expanded_full.brier_skill_score > 0.0,
        "expanded_log_loss_improved": expanded_full.log_loss < short_full.log_loss,
        "expanded_ece_improved": expanded_full.ece_10 < short_full.ece_10,
        "expanded_positive_pnl": expanded_full.pnl_wei > 0,
        "expanded_positive_roi": (
            expanded_full.roi_ppm is not None and expanded_full.roi_ppm > 0
        ),
        "best_brier_feature_set_stable": bool(
            stability["same_best_brier_feature_set"]
        ),
        "best_pnl_feature_set_stable": bool(stability["same_best_pnl_feature_set"]),
        "brier_ranking_stable": bool(stability["brier_ranking_stable"]),
        "pnl_ranking_stable": bool(stability["pnl_ranking_stable"]),
    }
    return EconomicWindowComparison(
        short_window=short,
        expanded_window=expanded,
        full_v1_delta=delta,
        feature_stability=stability,
        interpretation_flags=flags,
    )
