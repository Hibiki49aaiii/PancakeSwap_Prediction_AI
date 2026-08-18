from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from pancake_prediction.stage5_evidence import (
    Stage5ForkEvidence,
    evaluate_stage5b_fork_gate,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify observed Stage 5B local-BSC-fork evidence."
    )
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    evidence = Stage5ForkEvidence.from_path(args.evidence)
    report = evaluate_stage5b_fork_gate(
        ledger_path=args.db,
        evidence=evidence,
        expected_source_sha=str(args.source_sha),
    )
    print(json.dumps(report.as_dict(), sort_keys=True, separators=(",", ":")))
    return 0 if report.ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
