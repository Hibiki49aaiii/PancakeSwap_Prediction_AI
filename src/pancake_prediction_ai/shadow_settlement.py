from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from .economics import Side
from .event_store import EventRecord, EventStore, StoredEvent
from .onchain_collector import (
    read_prediction_buffer_seconds_at_anchor,
    read_prediction_round_at_anchor,
)
from .pancake_contract import BNB_CHAIN_ID, BNB_PREDICTION_CONTRACT, PredictionRoundState
from .read_only_rpc import ReadOnlyJsonRpcClient
from .rpc_snapshot import BlockAnchor, fetch_block_anchor
from .shadow_economics import ShadowEconomicAction


ClockNs = Callable[[], int]
AnchorFetcher = Callable[[ReadOnlyJsonRpcClient], BlockAnchor]
RoundReader = Callable[..., PredictionRoundState]
BufferReader = Callable[..., int]


class ShadowSettlementStatus(StrEnum):
    PENDING = "PENDING"
    SETTLED = "SETTLED"
    ALREADY_SETTLED = "ALREADY_SETTLED"
    ANOMALY = "ANOMALY"


class ShadowRoundResolution(StrEnum):
    BULL = "BULL"
    BEAR = "BEAR"
    TIE = "TIE"
    REFUND = "REFUND"


@dataclass(frozen=True, slots=True)
class ShadowEconomicSettlementResult:
    round_id: int
    status: ShadowSettlementStatus
    resolution: ShadowRoundResolution | None
    action: ShadowEconomicAction
    selected_side: Side | None
    anchor_number: int | None
    anchor_hash: str | None
    settled_fee_units: int | None
    pnl_if_executed_wei: int | None
    probability_adjusted_pnl_wei: float | None
    blockers: tuple[str, ...]
    stored_event: StoredEvent | None


def _int(payload: dict[str, object], field: str) -> int:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"shadow economic decision {field} must be integer")
    return value


def _float(payload: dict[str, object], field: str) -> float:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"shadow economic decision {field} must be numeric")
    return float(value)


def _optional_non_negative_int(payload: dict[str, object], field: str) -> int | None:
    value = payload.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"shadow economic decision {field} must be non-negative integer or null")
    return value


def _economic_decision(store: EventStore, round_id: int) -> StoredEvent:
    matches: list[StoredEvent] = []
    for stored in store.read_all_ingest_order():
        event = stored.event
        if event.source != "shadow" or event.topic != "shadow.economic_decision":
            continue
        value = event.payload.get("round_id")
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("stored shadow economic round_id is invalid")
        if value == round_id:
            matches.append(stored)
    if not matches:
        raise ValueError(f"missing shadow economic decision for round {round_id}")
    if len(matches) != 1:
        raise ValueError(f"duplicate shadow economic decisions for round {round_id}")
    return matches[0]


def _existing_settlement(store: EventStore, round_id: int) -> StoredEvent | None:
    matches = []
    for stored in store.read_all_ingest_order():
        event = stored.event
        if event.source == "shadow" and event.topic == "shadow.economic_settlement":
            value = event.payload.get("round_id")
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError("stored shadow settlement round_id is invalid")
            if value == round_id:
                matches.append(stored)
    if len(matches) > 1:
        raise ValueError(f"duplicate shadow economic settlements for round {round_id}")
    return None if not matches else matches[0]


def _decision_fields(
    decision: StoredEvent,
) -> tuple[ShadowEconomicAction, Side | None, int, int, float, int | None]:
    payload = decision.event.payload
    try:
        action = ShadowEconomicAction(str(payload["action"]))
    except (KeyError, ValueError) as exc:
        raise ValueError("shadow economic action is invalid") from exc
    side_raw = payload.get("selected_side")
    if action is ShadowEconomicAction.BET:
        try:
            side = Side(str(side_raw))
        except ValueError as exc:
            raise ValueError("BET shadow economic decision requires valid selected_side") from exc
    else:
        if side_raw is not None:
            raise ValueError("ABSTAIN shadow economic decision cannot have selected_side")
        side = None
    stake = _int(payload, "stake_wei")
    if stake <= 0:
        raise ValueError("shadow economic stake_wei must be positive")
    pool = payload.get("pool")
    if not isinstance(pool, dict):
        raise ValueError("shadow economic decision pool is invalid")
    treasury_fee_ppm = _int(pool, "treasury_fee_ppm")
    if treasury_fee_ppm < 0 or treasury_fee_ppm % 100 != 0:
        raise ValueError("decision treasury fee ppm cannot map to contract units")
    decision_fee_units = treasury_fee_ppm // 100
    if not 0 <= decision_fee_units <= 1000:
        raise ValueError("decision treasury fee units are outside contract bounds")
    assumed = payload.get("assumed_execution")
    if not isinstance(assumed, dict):
        raise ValueError("shadow economic execution assumptions are invalid")
    gas = _int(assumed, "gas_cost_wei")
    p_exec = _float(assumed, "execution_success_probability")
    claim_or_refund_gas = _optional_non_negative_int(
        assumed,
        "claim_or_refund_gas_cost_wei",
    )
    if gas < 0 or not 0.0 <= p_exec <= 1.0:
        raise ValueError("shadow economic execution assumptions are invalid")
    return action, side, stake, gas, p_exec, claim_or_refund_gas


def _resolution(round_state: PredictionRoundState) -> ShadowRoundResolution:
    if round_state.close_price > round_state.lock_price:
        return ShadowRoundResolution.BULL
    if round_state.close_price < round_state.lock_price:
        return ShadowRoundResolution.BEAR
    return ShadowRoundResolution.TIE


def _validate_settled_reward_fields(
    round_state: PredictionRoundState,
    resolution: ShadowRoundResolution,
) -> None:
    if resolution is ShadowRoundResolution.BULL:
        if round_state.reward_base_cal_amount_wei != round_state.bull_amount_wei:
            raise ValueError("settled BULL reward base does not equal bull pool")
    elif resolution is ShadowRoundResolution.BEAR:
        if round_state.reward_base_cal_amount_wei != round_state.bear_amount_wei:
            raise ValueError("settled BEAR reward base does not equal bear pool")
    elif resolution is ShadowRoundResolution.TIE:
        if round_state.reward_base_cal_amount_wei != 0 or round_state.reward_amount_wei != 0:
            raise ValueError("TIE settlement must have zero reward base and reward amount")


def _infer_fee_units(round_state: PredictionRoundState) -> tuple[int, ...]:
    total = round_state.total_amount_wei
    reward = round_state.reward_amount_wei
    return tuple(
        fee
        for fee in range(1001)
        if total - (total * fee) // 10_000 == reward
    )


def _source_snapshot_event(
    *,
    round_state: PredictionRoundState,
    anchor: BlockAnchor,
    observed_at_ns: int,
    prediction_contract: str,
) -> EventRecord:
    return EventRecord(
        event_id=(
            f"pancake:prediction:settlement_snapshot:{prediction_contract.lower()}:"
            f"{round_state.epoch}:{anchor.number}"
        ),
        source="pancake_prediction",
        topic="prediction.settlement_snapshot",
        event_time_ns=anchor.timestamp_s * 1_000_000_000,
        observed_at_ns=observed_at_ns,
        payload={
            "contract_address": prediction_contract.lower(),
            "block_number": anchor.number,
            "block_hash": anchor.block_hash,
            "block_timestamp_s": anchor.timestamp_s,
            "epoch": round_state.epoch,
            "start_timestamp": round_state.start_timestamp,
            "lock_timestamp": round_state.lock_timestamp,
            "close_timestamp": round_state.close_timestamp,
            "lock_price": round_state.lock_price,
            "close_price": round_state.close_price,
            "lock_oracle_id": round_state.lock_oracle_id,
            "close_oracle_id": round_state.close_oracle_id,
            "total_amount_wei": round_state.total_amount_wei,
            "bull_amount_wei": round_state.bull_amount_wei,
            "bear_amount_wei": round_state.bear_amount_wei,
            "reward_base_cal_amount_wei": round_state.reward_base_cal_amount_wei,
            "reward_amount_wei": round_state.reward_amount_wei,
            "oracle_called": round_state.oracle_called,
        },
    )


def _already_result(
    decision: StoredEvent,
    settlement: StoredEvent,
) -> ShadowEconomicSettlementResult:
    action, side, _stake, _gas, _p_exec, _claim_gas = _decision_fields(decision)
    payload = settlement.event.payload
    resolution = ShadowRoundResolution(str(payload["resolution"]))
    return ShadowEconomicSettlementResult(
        round_id=_int(payload, "round_id"),
        status=ShadowSettlementStatus.ALREADY_SETTLED,
        resolution=resolution,
        action=action,
        selected_side=side,
        anchor_number=_int(payload, "block_number"),
        anchor_hash=str(payload["block_hash"]),
        settled_fee_units=(
            None
            if payload.get("settled_fee_units") is None
            else _int(payload, "settled_fee_units")
        ),
        pnl_if_executed_wei=_int(payload, "pnl_if_executed_wei"),
        probability_adjusted_pnl_wei=_float(
            payload,
            "probability_adjusted_pnl_wei",
        ),
        blockers=(),
        stored_event=settlement,
    )


def reconcile_shadow_economic_round(
    store: EventStore,
    client: ReadOnlyJsonRpcClient,
    *,
    round_id: int,
    prediction_contract: str = BNB_PREDICTION_CONTRACT,
    clock_ns: ClockNs = time.time_ns,
    anchor_fetcher: AnchorFetcher = fetch_block_anchor,
    round_reader: RoundReader = read_prediction_round_at_anchor,
    buffer_reader: BufferReader = read_prediction_buffer_seconds_at_anchor,
) -> ShadowEconomicSettlementResult:
    """Reconcile one paper decision against an observed on-chain round state.

    `pnl_if_executed_wei` is a deterministic paper result conditional on the
    simulated bet having executed. `probability_adjusted_pnl_wei` additionally
    applies the decision-time execution-success probability and therefore still
    contains an explicit assumption. Claim/refund gas is included when it was
    fixed in the decision-time paper policy; otherwise winning/refund rounds are
    explicitly marked as not fully costed.
    """

    if store.mode != "observed":
        raise ValueError("shadow settlement reconciliation requires observed Event Store")
    if round_id < 0:
        raise ValueError("round_id must be non-negative")
    if client.chain_id() != BNB_CHAIN_ID:
        raise ValueError(f"expected BNB chain id {BNB_CHAIN_ID}")
    decision = _economic_decision(store, round_id)
    existing = _existing_settlement(store, round_id)
    if existing is not None:
        return _already_result(decision, existing)

    action, side, stake, gas, p_exec, claim_or_refund_gas = _decision_fields(decision)
    anchor = anchor_fetcher(client)
    round_state = round_reader(
        client,
        anchor=anchor,
        epoch=round_id,
        prediction_contract=prediction_contract,
    )
    if round_state.start_timestamp == 0:
        return ShadowEconomicSettlementResult(
            round_id,
            ShadowSettlementStatus.PENDING,
            None,
            action,
            side,
            anchor.number,
            anchor.block_hash,
            None,
            None,
            None,
            ("round_unavailable",),
            None,
        )

    if not round_state.oracle_called:
        if round_state.close_timestamp == 0:
            return ShadowEconomicSettlementResult(
                round_id,
                ShadowSettlementStatus.PENDING,
                None,
                action,
                side,
                anchor.number,
                anchor.block_hash,
                None,
                None,
                None,
                ("round_not_closed",),
                None,
            )
        buffer_seconds = buffer_reader(
            client,
            anchor=anchor,
            prediction_contract=prediction_contract,
        )
        if anchor.timestamp_s <= round_state.close_timestamp + buffer_seconds:
            return ShadowEconomicSettlementResult(
                round_id,
                ShadowSettlementStatus.PENDING,
                None,
                action,
                side,
                anchor.number,
                anchor.block_hash,
                None,
                None,
                None,
                ("refund_window_not_open",),
                None,
            )
        resolution = ShadowRoundResolution.REFUND
        settled_fee_units = None
    else:
        resolution = _resolution(round_state)
        try:
            _validate_settled_reward_fields(round_state, resolution)
        except ValueError as exc:
            return ShadowEconomicSettlementResult(
                round_id,
                ShadowSettlementStatus.ANOMALY,
                resolution,
                action,
                side,
                anchor.number,
                anchor.block_hash,
                None,
                None,
                None,
                (str(exc),),
                None,
            )
        settled_fee_units = None
        if resolution in {ShadowRoundResolution.BULL, ShadowRoundResolution.BEAR}:
            candidates = _infer_fee_units(round_state)
            if len(candidates) != 1:
                return ShadowEconomicSettlementResult(
                    round_id,
                    ShadowSettlementStatus.ANOMALY,
                    resolution,
                    action,
                    side,
                    anchor.number,
                    anchor.block_hash,
                    None,
                    None,
                    None,
                    (f"settlement_fee_not_uniquely_inferable:{len(candidates)}",),
                    None,
                )
            settled_fee_units = candidates[0]

    pnl_if_executed = 0
    gross_payout = 0
    applied_claim_or_refund_gas = 0
    claim_or_refund_gas_modeled = True
    if action is ShadowEconomicAction.BET:
        assert side is not None
        if resolution is ShadowRoundResolution.REFUND:
            gross_payout = stake
            if claim_or_refund_gas is None:
                claim_or_refund_gas_modeled = False
            else:
                applied_claim_or_refund_gas = claim_or_refund_gas
            pnl_if_executed = -gas - applied_claim_or_refund_gas
        else:
            won = (
                side is Side.BULL and resolution is ShadowRoundResolution.BULL
            ) or (
                side is Side.BEAR and resolution is ShadowRoundResolution.BEAR
            )
            if won:
                if settled_fee_units is None:
                    return ShadowEconomicSettlementResult(
                        round_id,
                        ShadowSettlementStatus.ANOMALY,
                        resolution,
                        action,
                        side,
                        anchor.number,
                        anchor.block_hash,
                        None,
                        None,
                        None,
                        ("winning_settlement_missing_fee",),
                        None,
                    )
                winning_pool = (
                    round_state.bull_amount_wei
                    if side is Side.BULL
                    else round_state.bear_amount_wei
                ) + stake
                simulated_total = round_state.total_amount_wei + stake
                treasury = (simulated_total * settled_fee_units) // 10_000
                distributable = simulated_total - treasury
                if winning_pool <= 0:
                    raise AssertionError("simulated winning pool must be positive")
                gross_payout = (stake * distributable) // winning_pool
                if claim_or_refund_gas is None:
                    claim_or_refund_gas_modeled = False
                else:
                    applied_claim_or_refund_gas = claim_or_refund_gas
                pnl_if_executed = (
                    gross_payout
                    - stake
                    - gas
                    - applied_claim_or_refund_gas
                )
            else:
                pnl_if_executed = -stake - gas

    probability_adjusted = p_exec * pnl_if_executed
    observed_at_ns = clock_ns()
    if observed_at_ns < decision.event.observed_at_ns:
        raise ValueError("settlement observation clock predates economic decision")

    source = _source_snapshot_event(
        round_state=round_state,
        anchor=anchor,
        observed_at_ns=observed_at_ns,
        prediction_contract=prediction_contract,
    )
    decision_payload = decision.event.payload
    pool_payload = decision_payload.get("pool")
    assert isinstance(pool_payload, dict)
    decision_fee_ppm = _int(pool_payload, "treasury_fee_ppm")
    decision_fee_units = decision_fee_ppm // 100
    settlement = EventRecord(
        event_id=f"shadow:economic_settlement:{round_id}",
        source="shadow",
        topic="shadow.economic_settlement",
        event_time_ns=anchor.timestamp_s * 1_000_000_000,
        observed_at_ns=observed_at_ns,
        payload={
            "round_id": round_id,
            "resolution": resolution.value,
            "action": action.value,
            "selected_side": None if side is None else side.value,
            "stake_wei": stake,
            "gas_cost_wei": gas,
            "claim_or_refund_gas_cost_wei": claim_or_refund_gas,
            "claim_or_refund_gas_applied_wei": applied_claim_or_refund_gas,
            "execution_success_probability": p_exec,
            "pnl_if_executed_wei": pnl_if_executed,
            "probability_adjusted_pnl_wei": probability_adjusted,
            "gross_payout_if_executed_wei": gross_payout,
            "economic_decision_event_id": decision.event.event_id,
            "settlement_snapshot_event_id": source.event_id,
            "block_number": anchor.number,
            "block_hash": anchor.block_hash,
            "block_timestamp_s": anchor.timestamp_s,
            "decision_fee_units": decision_fee_units,
            "settled_fee_units": settled_fee_units,
            "fee_changed_from_decision": (
                False
                if settled_fee_units is None
                else settled_fee_units != decision_fee_units
            ),
            "final_pool_excluding_paper_stake": {
                "total_amount_wei": round_state.total_amount_wei,
                "bull_amount_wei": round_state.bull_amount_wei,
                "bear_amount_wei": round_state.bear_amount_wei,
                "reward_base_cal_amount_wei": round_state.reward_base_cal_amount_wei,
                "reward_amount_wei": round_state.reward_amount_wei,
            },
            "post_decision_flow_replaced_by_observed_final_pool": True,
            "claim_or_refund_gas_modeled": claim_or_refund_gas_modeled,
        },
    )
    stored_source, stored_settlement = store.append_many((source, settlement))
    if stored_settlement.prev_hash != stored_source.event_hash:
        raise AssertionError(
            "derived settlement must chain directly to source settlement snapshot"
        )
    if not store.verify_chain():
        raise RuntimeError("Event Store hash chain failed after shadow settlement")

    return ShadowEconomicSettlementResult(
        round_id=round_id,
        status=ShadowSettlementStatus.SETTLED,
        resolution=resolution,
        action=action,
        selected_side=side,
        anchor_number=anchor.number,
        anchor_hash=anchor.block_hash,
        settled_fee_units=settled_fee_units,
        pnl_if_executed_wei=pnl_if_executed,
        probability_adjusted_pnl_wei=probability_adjusted,
        blockers=(),
        stored_event=stored_settlement,
    )
