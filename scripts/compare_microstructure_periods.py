from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import cast


def _load(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected object in {path}")
    return cast(dict[str, object], payload)


def _variant_map(payload: dict[str, object]) -> dict[str, dict[str, object]]:
    raw = payload.get("variants")
    if not isinstance(raw, list):
        raise ValueError("ablation evidence is missing variants")
    result: dict[str, dict[str, object]] = {}
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("variant entry must be an object")
        row = cast(dict[str, object], item)
        name = row.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("variant entry is missing name")
        if name in result:
            raise ValueError(f"duplicate variant name: {name}")
        result[name] = row
    return result


def _nested_number(row: dict[str, object], section: str, key: str) -> float:
    raw_section = row.get(section)
    if not isinstance(raw_section, dict):
        raise ValueError(f"variant is missing {section}")
    value = cast(dict[str, object], raw_section).get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"variant {section}.{key} must be numeric")
    return float(value)


def _baseline_roi(payload: dict[str, object]) -> int:
    section = payload.get("baseline_economics")
    if not isinstance(section, dict):
        raise ValueError("evidence is missing baseline_economics")
    value = cast(dict[str, object], section).get("roi_ppm")
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("baseline roi_ppm must be an integer")
    return value


def _digest(payload: dict[str, object]) -> str:
    raw = (
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    ).encode()
    return hashlib.sha256(raw).hexdigest()


def compare_periods(
    exploratory: dict[str, object],
    confirmation: dict[str, object],
) -> dict[str, object]:
    if confirmation.get("evidence_role") != "independent_confirmation":
        raise ValueError("confirmation evidence role is not independent_confirmation")
    if confirmation.get("variant_set_precommitted_before_exploratory_result_review") is not True:
        raise ValueError("confirmation variant set was not recorded as precommitted")

    first = _variant_map(exploratory)
    second = _variant_map(confirmation)
    if tuple(first) != tuple(second):
        raise ValueError("variant sets/order differ across periods")
    first_baseline_roi = _baseline_roi(exploratory)
    second_baseline_roi = _baseline_roi(confirmation)

    rows: list[dict[str, object]] = []
    stable: list[str] = []
    black_both: list[str] = []
    for name in first:
        first_skill = _nested_number(first[name], "probability", "brier_skill_score")
        second_skill = _nested_number(second[name], "probability", "brier_skill_score")
        first_roi = round(_nested_number(first[name], "economics", "roi_ppm"))
        second_roi = round(_nested_number(second[name], "economics", "roi_ppm"))
        first_pnl = round(_nested_number(first[name], "economics", "pnl_wei"))
        second_pnl = round(_nested_number(second[name], "economics", "pnl_wei"))
        positive_skill_both = first_skill > 0.0 and second_skill > 0.0
        roi_improved_both = (
            first_roi > first_baseline_roi and second_roi > second_baseline_roi
        )
        positive_pnl_both = first_pnl > 0 and second_pnl > 0
        stable_candidate = positive_skill_both and roi_improved_both
        if stable_candidate:
            stable.append(name)
        if positive_pnl_both:
            black_both.append(name)
        rows.append(
            {
                "name": name,
                "exploratory_brier_skill": first_skill,
                "confirmation_brier_skill": second_skill,
                "exploratory_roi_ppm": first_roi,
                "confirmation_roi_ppm": second_roi,
                "positive_brier_skill_both_periods": positive_skill_both,
                "roi_better_than_period_baseline_both_periods": roi_improved_both,
                "positive_pnl_both_periods": positive_pnl_both,
                "stable_research_candidate": stable_candidate,
            }
        )

    payload: dict[str, object] = {
        "evidence_version": 1,
        "source_class": "third_party_historical_benchmark",
        "authoritative": False,
        "profitability_gate_eligible": False,
        "exploratory_selection": exploratory.get("selection"),
        "independent_confirmation_selection": confirmation.get("selection"),
        "exploratory_baseline_roi_ppm": first_baseline_roi,
        "confirmation_baseline_roi_ppm": second_baseline_roi,
        "variant_set_precommitted_before_exploratory_result_review": True,
        "variants": rows,
        "stable_research_candidates": stable,
        "positive_pnl_both_periods": black_both,
        "any_stable_research_candidate": bool(stable),
        "any_positive_pnl_both_periods": bool(black_both),
    }
    payload["cross_period_digest"] = _digest(payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exploratory", type=Path, required=True)
    parser.add_argument("--confirmation", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = compare_periods(_load(args.exploratory), _load(args.confirmation))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
