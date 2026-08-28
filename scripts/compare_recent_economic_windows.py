from __future__ import annotations

import argparse
import json
from pathlib import Path

from pancake_prediction.window_comparison import compare_economic_windows


def _load(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare fail-closed recent economic robustness evidence across windows."
    )
    parser.add_argument("--short-evidence", type=Path, required=True)
    parser.add_argument("--expanded-evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--short-label", default="one_day")
    parser.add_argument("--expanded-label", default="three_day")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = compare_economic_windows(
        _load(args.short_evidence),
        _load(args.expanded_evidence),
        short_label=args.short_label,
        expanded_label=args.expanded_label,
    )
    rendered = json.dumps(report.as_dict(), indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
