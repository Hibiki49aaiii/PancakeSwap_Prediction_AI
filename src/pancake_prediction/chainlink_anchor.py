from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast

from .recent_bootstrap import ChainlinkRouteAnchor


def _object(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return cast(dict[str, Any], value)


def load_chainlink_route_anchor(path: Path, *, market: str) -> ChainlinkRouteAnchor:
    """Load and bind a persisted successful Chainlink route proof by exact bytes."""

    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid Chainlink route anchor JSON: {exc}") from exc
    payload = _object(decoded, field="anchor evidence")

    if payload.get("success") is not True:
        raise ValueError("Chainlink route anchor evidence is not successful")
    workflow_outcome = payload.get("workflow_outcome")
    if workflow_outcome not in {None, "success"}:
        raise ValueError("Chainlink route anchor workflow outcome is not successful")
    if payload.get("chainlink_collected") is not True:
        raise ValueError("Chainlink route anchor did not collect Chainlink events")
    if str(payload.get("market", "")) != market:
        raise ValueError("Chainlink route anchor market does not match requested market")

    selected = _object(payload.get("selected"), field="anchor selected")
    report = _object(selected.get("report"), field="anchor selected.report")
    if report.get("authoritative_prediction_events") is not True:
        raise ValueError("Chainlink route anchor lacks authoritative Prediction events")
    if report.get("chainlink_collected") is not True:
        raise ValueError("Chainlink route anchor report did not collect Chainlink events")

    proof = _object(
        report.get("oracle_stability_proof"),
        field="anchor selected.report.oracle_stability_proof",
    )
    if int(proof.get("new_oracle_events", -1)) != 0:
        raise ValueError("Chainlink route anchor contains a Prediction oracle change")
    if int(proof.get("aggregator_confirmed_events", -1)) != 0:
        raise ValueError("Chainlink route anchor contains a Chainlink aggregator change")

    anchor_block = int(proof.get("from_block", 0))
    proof_through_block = int(proof.get("through_block", 0))
    if anchor_block <= 0 or proof_through_block < anchor_block:
        raise ValueError("Chainlink route anchor has an invalid proven block range")
    oracle_proxy = str(proof.get("oracle", "")).lower()
    chainlink_aggregator = str(proof.get("chainlink_aggregator", "")).lower()

    collection = _object(report.get("collection"), field="anchor selected.report.collection")
    oracle_addresses = collection.get("oracle_addresses")
    chainlink_addresses = collection.get("chainlink_event_addresses")
    if oracle_addresses != [oracle_proxy]:
        raise ValueError("Chainlink route anchor proxy disagrees with collection evidence")
    if chainlink_addresses != [chainlink_aggregator]:
        raise ValueError("Chainlink route anchor aggregator disagrees with collection evidence")
    if int(collection.get("chainlink_events_inserted", 0)) <= 0:
        raise ValueError("Chainlink route anchor contains no AnswerUpdated events")

    return ChainlinkRouteAnchor(
        oracle_proxy=oracle_proxy,
        chainlink_aggregator=chainlink_aggregator,
        anchor_block=anchor_block,
        evidence_sha256=digest,
    )
