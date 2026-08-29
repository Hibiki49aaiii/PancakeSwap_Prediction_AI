from __future__ import annotations

import argparse
import json
from pathlib import Path

from pancake_prediction.shadow_campaign import (
    ShadowCampaignPolicy,
    build_shadow_campaign_evidence,
    evaluate_shadow_campaign,
)
from pancake_prediction.shadow_ledger import ShadowLedgerStore

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build fail-closed Stage 4 shadow campaign evidence from an append-only ledger."
    )
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--purge-rounds", type=int, default=2)
    parser.add_argument("--min-predictions", type=int, default=1_000)
    parser.add_argument("--min-settlements", type=int, default=900)
    parser.add_argument("--min-probability-scored", type=int, default=900)
    parser.add_argument("--min-actionable-predictions", type=int, default=200)
    parser.add_argument(
        "--min-decision-span-seconds",
        type=int,
        default=7 * 24 * 60 * 60,
    )
    parser.add_argument("--max-unresolved-ppm", type=int, default=100_000)
    parser.add_argument("--max-model-ids", type=int, default=1)
    parser.add_argument("--max-feature-set-ids", type=int, default=1)
    parser.add_argument("--allow-single-direction", action="store_true")
    parser.add_argument("--allow-missing-actionable-pnl", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.purge_rounds < 0:
        raise SystemExit("--purge-rounds must be non-negative")
    if args.min_decision_span_seconds < 0:
        raise SystemExit("--min-decision-span-seconds must be non-negative")
    if not args.db.is_file():
        raise SystemExit(f"shadow ledger does not exist: {args.db}")

    store = ShadowLedgerStore(args.db)
    store.initialize()
    audit = store.audit(purge_rounds=int(args.purge_rounds))
    policy = ShadowCampaignPolicy(
        min_predictions=int(args.min_predictions),
        min_settlements=int(args.min_settlements),
        min_probability_scored=int(args.min_probability_scored),
        min_actionable_predictions=int(args.min_actionable_predictions),
        min_decision_span_ms=int(args.min_decision_span_seconds) * 1_000,
        max_unresolved_ppm=int(args.max_unresolved_ppm),
        require_both_directions=not bool(args.allow_single_direction),
        require_complete_actionable_pnl=not bool(args.allow_missing_actionable_pnl),
        max_model_ids=int(args.max_model_ids),
        max_feature_set_ids=int(args.max_feature_set_ids),
    )
    report = evaluate_shadow_campaign(audit, policy)
    payload = build_shadow_campaign_evidence(args.db, report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    temporary = args.output.with_name(args.output.name + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(args.output)
    print(rendered, end="")
    return 0 if report.gate_ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
