from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Sequence

from .dataset_artifact import load_historical_dataset_artifact
from .evaluation_artifact import (
    BaselineEvaluationConfig,
    evaluate_historical_dataset_artifact,
    load_baseline_evaluation_artifact,
)
from .event_store import EventStore
from .historical_pipeline import HistoricalPipeline, HistoricalPipelineConfig
from .research_manifest import build_research_run_manifest
from .tie_prior import TiePriorPolicy
from .trained_model_artifact import (
    PromotedModelConfig,
    load_promoted_model_artifact,
    train_promoted_model_artifact,
)


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def _non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def _tie_prior(args: argparse.Namespace) -> TiePriorPolicy:
    return TiePriorPolicy(
        z_score=args.tie_z_score,
        directional_alpha=args.tie_directional_alpha,
        minimum_tie_probability=args.minimum_tie_probability,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ppai-artifact",
        description="Build SHA-256-bound Pancake Prediction research artifacts",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    dataset = sub.add_parser(
        "build-dataset",
        help="Freeze a populated reconstructed Event Store into a historical dataset artifact",
    )
    dataset.add_argument("--store", type=Path, required=True)
    dataset.add_argument("--dataset-id", required=True)
    dataset.add_argument("--decision-lead-ns", type=_positive_int, required=True)
    dataset.add_argument("--binance-latency-ns", type=_non_negative_int, required=True)
    dataset.add_argument("--onchain-latency-ns", type=_non_negative_int, required=True)
    dataset.add_argument("--output", type=Path, required=True)

    evaluate = sub.add_parser(
        "evaluate-oos",
        help="Run purged availability-safe baseline OOS evaluation from a dataset artifact",
    )
    evaluate.add_argument("--dataset", type=Path, required=True)
    evaluate.add_argument("--output", type=Path, required=True)
    evaluate.add_argument("--min-train-size", type=_positive_int, required=True)
    evaluate.add_argument("--test-size", type=_positive_int, required=True)
    evaluate.add_argument("--purge-size", type=_non_negative_int, required=True)
    evaluate.add_argument("--calibration-size", type=_positive_int, required=True)
    evaluate.add_argument("--step-size", type=_positive_int)
    evaluate.add_argument("--max-train-size", type=_positive_int)
    evaluate.add_argument("--learning-rate", type=_positive_float, default=0.05)
    evaluate.add_argument("--epochs", type=_positive_int, default=500)
    evaluate.add_argument("--l2", type=float, default=1e-4)
    evaluate.add_argument("--prior-strength", type=_positive_float, default=20.0)
    evaluate.add_argument("--tie-z-score", type=_positive_float, default=1.96)
    evaluate.add_argument("--tie-directional-alpha", type=_positive_float, default=0.5)
    evaluate.add_argument("--minimum-tie-probability", type=float, default=1e-6)

    promote = sub.add_parser(
        "promote-model",
        help="Train and freeze a promoted shadow-model artifact using labels available by cutoff",
    )
    promote.add_argument("--dataset", type=Path, required=True)
    promote.add_argument("--output", type=Path, required=True)
    promote.add_argument("--training-cutoff-ns", type=_positive_int, required=True)
    promote.add_argument("--calibration-size", type=_positive_int, required=True)
    promote.add_argument("--learning-rate", type=_positive_float, default=0.05)
    promote.add_argument("--epochs", type=_positive_int, default=500)
    promote.add_argument("--l2", type=float, default=1e-4)
    promote.add_argument("--prior-strength", type=_positive_float, default=20.0)
    promote.add_argument("--tie-z-score", type=_positive_float, default=1.96)
    promote.add_argument("--tie-directional-alpha", type=_positive_float, default=0.5)
    promote.add_argument("--minimum-tie-probability", type=float, default=1e-6)

    manifest = sub.add_parser(
        "build-manifest",
        help="Bind dataset, OOS evaluation, and promoted model into one reconstructed research manifest",
    )
    manifest.add_argument("--dataset", type=Path, required=True)
    manifest.add_argument("--evaluation", type=Path, required=True)
    manifest.add_argument("--model", type=Path, required=True)
    manifest.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    generated_at_ns = time.time_ns()

    if args.command == "build-dataset":
        config = HistoricalPipelineConfig(
            dataset_id=args.dataset_id,
            decision_lead_ns=args.decision_lead_ns,
            assumed_binance_latency_ns=args.binance_latency_ns,
            assumed_onchain_latency_ns=args.onchain_latency_ns,
        )
        with EventStore(args.store, mode="reconstructed") as store:
            pipeline = HistoricalPipeline(store, config)
            artifact = pipeline.build_dataset_artifact(generated_at_ns=generated_at_ns)
        artifact.write(args.output)
        print(
            f"dataset sha256={artifact.artifact_sha256} "
            f"examples={len(artifact.examples)} output={args.output}"
        )
        return 0

    if args.command == "evaluate-oos":
        if args.l2 < 0:
            raise SystemExit("--l2 must be non-negative")
        dataset = load_historical_dataset_artifact(args.dataset)
        config = BaselineEvaluationConfig(
            min_train_size=args.min_train_size,
            test_size=args.test_size,
            purge_size=args.purge_size,
            calibration_size=args.calibration_size,
            step_size=args.step_size,
            max_train_size=args.max_train_size,
            learning_rate=args.learning_rate,
            epochs=args.epochs,
            l2=args.l2,
            prior_strength=args.prior_strength,
            tie_prior_policy=_tie_prior(args),
        )
        artifact = evaluate_historical_dataset_artifact(
            dataset,
            generated_at_ns=generated_at_ns,
            config=config,
        )
        artifact.write(args.output)
        metrics = artifact.payload["result"]["aggregate_metrics"]
        print(
            f"oos sha256={artifact.artifact_sha256} "
            f"count={metrics['count']} brier={metrics['multiclass_brier_score']:.8f} "
            f"logloss={metrics['log_loss']:.8f} accuracy={metrics['top_label_accuracy']:.8f} "
            f"ece={metrics['expected_calibration_error']:.8f} output={args.output}"
        )
        return 0

    if args.command == "promote-model":
        if args.l2 < 0:
            raise SystemExit("--l2 must be non-negative")
        dataset = load_historical_dataset_artifact(args.dataset)
        config = PromotedModelConfig(
            calibration_size=args.calibration_size,
            learning_rate=args.learning_rate,
            epochs=args.epochs,
            l2=args.l2,
            prior_strength=args.prior_strength,
            tie_prior_policy=_tie_prior(args),
        )
        artifact = train_promoted_model_artifact(
            dataset,
            training_cutoff_ns=args.training_cutoff_ns,
            generated_at_ns=generated_at_ns,
            config=config,
        )
        artifact.write(args.output)
        print(
            f"model sha256={artifact.artifact_sha256} "
            f"eligible_rounds={artifact.payload['eligible_round_count']} output={args.output}"
        )
        return 0

    if args.command == "build-manifest":
        dataset = load_historical_dataset_artifact(args.dataset)
        evaluation = load_baseline_evaluation_artifact(args.evaluation)
        model = load_promoted_model_artifact(args.model)
        manifest = build_research_run_manifest(
            dataset,
            evaluation,
            model,
            generated_at_ns=generated_at_ns,
        )
        manifest.write(args.output)
        metrics = manifest.payload["oos_metrics"]
        print(
            f"manifest sha256={manifest.artifact_sha256} "
            f"dataset={manifest.payload['dataset_artifact_sha256']} "
            f"oos_count={metrics['count']} output={args.output}"
        )
        return 0

    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())