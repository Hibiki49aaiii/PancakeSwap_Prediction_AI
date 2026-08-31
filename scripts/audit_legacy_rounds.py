from __future__ import annotations

import argparse
import json
from pathlib import Path

from pancake_prediction.legacy_rounds import audit_legacy_rounds


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    report = audit_legacy_rounds(args.archive)
    payload = report.as_dict()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0 if report.structurally_ready and report.expected_epoch_envelope_ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
