from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from .economics import ExecutionCost, EVResult, PoolState, Side, evaluate_bet_ev
from .event_store import EventRecord, EventStore, StoredEvent
from .shadow_inference import ShadowInferenceResult


ClockNs = Callable[[], int]


class ShadowEconomicAction(StrEnum):
    BET = "BET"
    ABSTAIN = "ABSTAIN"


@dataclass(frozen=True, slots=True)
class ShadowEconomicPolicy:
    stake_wei: int
    gas_cost_wei: int = 0
    claim_or_refund_gas_cost_wei: int | None = None
    same_side_inflow_wei: int = 0
    opposite_side_inflow_wei: int = 0
    execution_success_probability: float = 1.0
    min_expected_return: float = 0.0

    def validate(self) -> None:
        if self.stake_wei <= 0:
            raise ValueError("shadow stake_wei must be positive")
        if self.gas_cost_wei < 0:
            raise ValueError("shadow gas_cost_wei must be non-negative")
        if (
            self.claim_or_refund_gas_cost_wei is not None
            and self.claim_or_refund_gas_cost_wei < 0
        ):
            raise ValueError("shadow claim_or_refund_gas_cost_wei must be non-negative")
        if self.same_side_inflow_wei < 0 or self.opposite_side_inflow_wei < 0:
            raise ValueError("shadow post-decision inflows must be non-negative")
        if not 0.0 <= self.execution_success_probability <= 1.0:
            raise ValueError("shadow execution_success_probability must be in [0, 1]")
        if not math.isfinite(self.min_expected_return) or self.min_expected_return < 0:
            raise ValueError("shadow min_expected_return must be non-negative finite")

    def execution_cost(self) -> ExecutionCost:
        self.validate()
        return ExecutionCost(
            gas_cost_wei=self.gas_cost_wei,
            claim_cost_if_win_wei=(
                0
                if self.claim_or_refund_gas_cost_wei is None
                else self.claim_or_refund_gas_cost_wei
            ),
            same_side_inflow_wei=self.same_side_inflow_wei,
            opposite_side_inflow_wei=self.opposite_side_inflow_wei,
            execution_success_probability=self.execution_success_probability,
        )


@dataclass(frozen=True, slots=True)
class ShadowEconomicDecisionResult:
    round_id: int
    action: ShadowEconomicAction
    selected_side: Side | None
    bull: EVResult
    bear: EVResult
    source_snapshot_tip_hash: str
    stored_event: StoredEvent


def _events_through_tip(store: EventStore, tip_hash: str) -> tuple[StoredEvent, ...]:
    events = store.read_all_ingest_order()
    for index, stored in enumerate(events):
        if stored.event_hash == tip_hash:
            return events[: index + 1]
    raise ValueError("shadow inference source snapshot tip is not present in Event Store")


def _integer(payload: dict | object, field: str) -> int:
    if not isinstance(payload, dict):
        raise ValueError("round snapshot payload must be an object")
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"round snapshot {field} must be integer")
    return value


def _round_pool_at_tip(
    store: EventStore,
    *,
    tip_hash: str,
    round_id: int,
) -> tuple[StoredEvent, PoolState]:
    events = _events_through_tip(store, tip_hash)
    for stored in reversed(events):
        event = stored.event
        if event.source != "pancake_prediction" or event.topic != "prediction.round_snapshot":
            continue
        epoch = event.payload.get("epoch")
        if isinstance(epoch, bool) or not isinstance(epoch, int):
            raise ValueError("round snapshot epoch must be integer")
        if epoch != round_id:
            continue
        pool = PoolState(
            bull_wei=_integer(event.payload, "bull_amount_wei"),
            bear_wei=_integer(event.payload, "bear_amount_wei"),
            treasury_fee_ppm=_integer(event.payload, "treasury_fee_ppm"),
        )
        pool.validate()
        total = _integer(event.payload, "total_amount_wei")
        if total != pool.bull_wei + pool.bear_wei:
            raise ValueError("round snapshot pool total invariant failed")
        return stored, pool
    raise ValueError(f"missing exact round snapshot for shadow round {round_id}")


def _ev_payload(result: EVResult) -> dict[str, float | str]:
    return {
        "side": result.side.value,
        "probability_win": result.probability_win,
        "gross_payout_if_win_wei": result.gross_payout_if_win_wei,
        "pnl_if_win_wei": result.pnl_if_win_wei,
        "pnl_if_lose_wei": result.pnl_if_lose_wei,
        "expected_pnl_if_executed_wei": result.expected_pnl_if_executed_wei,
        "expected_pnl_wei": result.expected_pnl_wei,
        "expected_return_on_stake": result.expected_return_on_stake,
        "break_even_probability": result.break_even_probability,
    }


def record_shadow_economic_decision(
    inference: ShadowInferenceResult,
    store: EventStore,
    *,
    policy: ShadowEconomicPolicy,
    clock_ns: ClockNs = time.time_ns,
) -> ShadowEconomicDecisionResult:
    """Turn one accepted model inference into a strictly simulated EV decision.

    The exact Pancake pool is recovered from the hash-chain tip that the model
    used, never from a later round observation or from floating-point features.
    Both sides are evaluated with identical stake/cost assumptions. A model may
    predict a direction yet still produce an explicit ABSTAIN when neither side
    clears the economic threshold. Optional claim/refund gas is fixed before the
    outcome and incorporated into win-side EV rather than added retrospectively.
    """

    if store.mode != "observed":
        raise ValueError("shadow economics requires observed Event Store")
    policy.validate()
    if not inference.accepted:
        raise ValueError("shadow economics requires an accepted model inference")
    if inference.round_id is None:
        raise ValueError("accepted shadow inference is missing round_id")
    if inference.probability_bull is None or inference.probability_tie is None:
        raise ValueError("accepted shadow inference is missing probabilities")
    if inference.source_snapshot_tip_hash is None:
        raise ValueError("accepted shadow inference is missing source snapshot tip")
    if inference.stored_decision is None:
        raise ValueError("accepted shadow inference is missing persisted model decision")
    if inference.stored_decision.prev_hash != inference.source_snapshot_tip_hash:
        raise ValueError("model decision is not directly chained to its declared source tip")

    round_snapshot, pool = _round_pool_at_tip(
        store,
        tip_hash=inference.source_snapshot_tip_hash,
        round_id=inference.round_id,
    )
    cost = policy.execution_cost()
    bull = evaluate_bet_ev(
        probability_bull=inference.probability_bull,
        probability_tie=inference.probability_tie,
        side=Side.BULL,
        stake_wei=policy.stake_wei,
        pool=pool,
        cost=cost,
    )
    bear = evaluate_bet_ev(
        probability_bull=inference.probability_bull,
        probability_tie=inference.probability_tie,
        side=Side.BEAR,
        stake_wei=policy.stake_wei,
        pool=pool,
        cost=cost,
    )
    best = bull if bull.expected_pnl_wei >= bear.expected_pnl_wei else bear
    if best.expected_return_on_stake >= policy.min_expected_return and best.expected_pnl_wei > 0:
        action = ShadowEconomicAction.BET
        selected_side: Side | None = best.side
    else:
        action = ShadowEconomicAction.ABSTAIN
        selected_side = None

    decided_at_ns = clock_ns()
    if decided_at_ns < inference.stored_decision.event.observed_at_ns:
        raise ValueError("shadow economic decision clock predates model decision")

    event = EventRecord(
        event_id=f"shadow:economic_decision:{inference.round_id}",
        source="shadow",
        topic="shadow.economic_decision",
        event_time_ns=decided_at_ns,
        observed_at_ns=decided_at_ns,
        payload={
            "round_id": inference.round_id,
            "action": action.value,
            "selected_side": None if selected_side is None else selected_side.value,
            "stake_wei": policy.stake_wei,
            "model_decision_event_id": inference.stored_decision.event.event_id,
            "promoted_model_artifact_sha256": inference.promoted_model_artifact_sha256,
            "source_snapshot_tip_hash": inference.source_snapshot_tip_hash,
            "source_round_snapshot_event_id": round_snapshot.event.event_id,
            "probability": {
                "bull": inference.probability_bull,
                "bear": inference.probability_bear,
                "tie": inference.probability_tie,
            },
            "pool": {
                "bull_wei": pool.bull_wei,
                "bear_wei": pool.bear_wei,
                "treasury_fee_ppm": pool.treasury_fee_ppm,
            },
            "assumed_execution": {
                "gas_cost_wei": policy.gas_cost_wei,
                "claim_or_refund_gas_cost_wei": policy.claim_or_refund_gas_cost_wei,
                "same_side_inflow_wei": policy.same_side_inflow_wei,
                "opposite_side_inflow_wei": policy.opposite_side_inflow_wei,
                "execution_success_probability": policy.execution_success_probability,
                "min_expected_return": policy.min_expected_return,
            },
            "bull_ev": _ev_payload(bull),
            "bear_ev": _ev_payload(bear),
        },
    )
    stored = store.append(event)
    return ShadowEconomicDecisionResult(
        round_id=inference.round_id,
        action=action,
        selected_side=selected_side,
        bull=bull,
        bear=bear,
        source_snapshot_tip_hash=inference.source_snapshot_tip_hash,
        stored_event=stored,
    )
