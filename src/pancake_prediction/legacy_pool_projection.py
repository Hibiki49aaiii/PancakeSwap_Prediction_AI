from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from statistics import median

from .absolute_pool_projection import AbsolutePoolProjectionConfig
from .backtest import PoolProjection
from .legacy_rounds import LegacyRoundRecord


def _model_id(
    config: AbsolutePoolProjectionConfig,
    training: tuple[LegacyRoundRecord, ...],
) -> str:
    payload = {
        "config": asdict(config),
        "training": [
            {
                "epoch": row.epoch,
                "bull_amount_wei": row.bull_amount_wei,
                "bear_amount_wei": row.bear_amount_wei,
            }
            for row in training
        ],
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:12]
    return f"legacy-prior-final-absolute-median-{digest}"


def _eligible_training_round(record: LegacyRoundRecord) -> bool:
    return (
        record.oracle_called
        and record.label in {"bull", "bear", "tie"}
        and record.total_amount_wei > 0
        and record.bull_amount_wei >= 0
        and record.bear_amount_wei >= 0
    )


def build_legacy_absolute_pool_projections(
    rounds: tuple[LegacyRoundRecord, ...],
    *,
    decision_lead_seconds: int = 20,
    config: AbsolutePoolProjectionConfig | None = None,
) -> dict[int, PoolProjection]:
    """Forecast final pools from older rounds only; never inspect target pools."""

    resolved = AbsolutePoolProjectionConfig() if config is None else config
    resolved.validate()
    if decision_lead_seconds <= 0:
        raise ValueError("decision_lead_seconds must be positive")
    ordered = tuple(sorted(rounds, key=lambda record: record.epoch))
    result: dict[int, PoolProjection] = {}

    for target in ordered:
        decision_timestamp = target.lock_timestamp - decision_lead_seconds
        if decision_timestamp <= target.start_timestamp:
            continue
        latest_allowed_epoch = target.epoch - resolved.purge_rounds - 1
        candidates = [
            prior
            for prior in ordered
            if prior.epoch <= latest_allowed_epoch
            and prior.close_timestamp < decision_timestamp
            and _eligible_training_round(prior)
        ]
        if len(candidates) < resolved.min_train_rounds:
            continue
        training = tuple(candidates[-resolved.window_rounds :])
        projected_bull = int(median(row.bull_amount_wei for row in training))
        projected_bear = int(median(row.bear_amount_wei for row in training))
        result[target.epoch] = PoolProjection(
            epoch=target.epoch,
            generated_at=decision_timestamp,
            projected_bull_wei=max(0, projected_bull),
            projected_bear_wei=max(0, projected_bear),
            model_id=_model_id(resolved, training),
            train_max_epoch=training[-1].epoch,
        )
    return result
