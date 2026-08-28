from __future__ import annotations

from dataclasses import asdict, dataclass

from .economics import ParimutuelQuote, gross_payout_if_win_wei
from .replay import ReplaySnapshot, RoundRecord
from .research_ledger import ResearchPredictionRecord, feature_digest
from .shadow_ledger import (
    ShadowLedgerEvent,
    ShadowLedgerStore,
    ShadowSettlementRecord,
    prediction_from_payload,
    settlement_from_payload,
)


@dataclass(frozen=True, slots=True)
class ShadowReconciliationReport:
    market: str
    prediction_count: int
    existing_settlement_count: int
    appended_settlement_count: int
    unresolved_count: int
    appended_events: tuple[ShadowLedgerEvent, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "market": self.market,
            "prediction_count": self.prediction_count,
            "existing_settlement_count": self.existing_settlement_count,
            "appended_settlement_count": self.appended_settlement_count,
            "unresolved_count": self.unresolved_count,
            "appended_events": [event.as_dict() for event in self.appended_events],
            "signing_enabled": False,
            "live_broadcast": False,
        }


def _strict_metadata_int(
    prediction: ResearchPredictionRecord,
    field: str,
) -> int:
    metadata = prediction.metadata
    if metadata is None:
        raise ValueError(
            f"shadow prediction {prediction.epoch} is missing economic metadata"
        )
    value = metadata.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(
            f"shadow prediction {prediction.epoch} metadata {field} must be an integer"
        )
    return value


def _round_source_digest(market: str, record: RoundRecord) -> str:
    return feature_digest(
        {
            "market": market,
            "epoch": record.epoch,
            "lock_block": record.lock_block,
            "lock_timestamp": record.lock_timestamp,
            "lock_round_id": record.lock_round_id,
            "lock_price": record.lock_price,
            "end_block": record.end_block,
            "end_timestamp": record.end_timestamp,
            "close_round_id": record.close_round_id,
            "close_price": record.close_price,
            "bull_amount_wei": record.bull_amount_wei,
            "bear_amount_wei": record.bear_amount_wei,
            "total_amount_wei": record.total_amount_wei,
            "reward_base_cal_amount_wei": record.reward_base_cal_amount_wei,
            "reward_amount_wei": record.reward_amount_wei,
            "treasury_amount_wei": record.treasury_amount_wei,
            "label": record.label,
            "issues": list(record.issues),
        }
    )


def _validate_round_integrity(
    prediction: ResearchPredictionRecord,
    record: RoundRecord,
) -> None:
    if record.epoch != prediction.epoch:
        raise ValueError("settlement round epoch does not match prediction")
    if record.end_timestamp is None or record.end_block is None:
        raise ValueError(f"round {record.epoch} is not settled")
    if record.label not in {"bull", "bear", "tie"}:
        raise ValueError(f"round {record.epoch} has unsupported outcome {record.label!r}")
    if record.bull_amount_wei < 0 or record.bear_amount_wei < 0:
        raise ValueError(f"round {record.epoch} has negative pool amounts")
    if record.total_amount_wei != record.bull_amount_wei + record.bear_amount_wei:
        raise ValueError(f"round {record.epoch} final pool total is inconsistent")
    if record.issues:
        raise ValueError(
            f"round {record.epoch} contains replay integrity issues: "
            + ", ".join(record.issues)
        )

    fee_bps = _strict_metadata_int(prediction, "treasury_fee_bps")
    if not 0 <= fee_bps < 10_000:
        raise ValueError(f"round {record.epoch} treasury fee is invalid")

    reward_fields = (
        record.reward_base_cal_amount_wei,
        record.reward_amount_wei,
        record.treasury_amount_wei,
    )
    if all(value is None for value in reward_fields):
        return
    if any(value is None for value in reward_fields):
        raise ValueError(f"round {record.epoch} has partial reward calculation fields")

    if record.label == "tie":
        expected_base = 0
        expected_reward = 0
        expected_treasury = record.total_amount_wei
    else:
        expected_base = (
            record.bull_amount_wei
            if record.label == "bull"
            else record.bear_amount_wei
        )
        expected_treasury = record.total_amount_wei * fee_bps // 10_000
        expected_reward = record.total_amount_wei - expected_treasury

    if (
        record.reward_base_cal_amount_wei != expected_base
        or record.reward_amount_wei != expected_reward
        or record.treasury_amount_wei != expected_treasury
    ):
        raise ValueError(
            f"round {record.epoch} reward calculation does not match decision-time fee"
        )


def _realized_shadow_pnl(
    prediction: ResearchPredictionRecord,
    record: RoundRecord,
) -> int:
    if prediction.action == "skip":
        return 0

    stake_wei = _strict_metadata_int(prediction, "stake_wei")
    bet_gas_wei = _strict_metadata_int(prediction, "bet_gas_wei")
    claim_gas_wei = _strict_metadata_int(prediction, "claim_gas_wei")
    fee_bps = _strict_metadata_int(prediction, "treasury_fee_bps")
    if stake_wei <= 0:
        raise ValueError(f"shadow prediction {prediction.epoch} stake must be positive")
    if bet_gas_wei < 0 or claim_gas_wei < 0:
        raise ValueError(f"shadow prediction {prediction.epoch} gas costs must be non-negative")

    if record.label != prediction.action:
        return -stake_wei - bet_gas_wei

    quote = ParimutuelQuote(
        side=prediction.action,
        side_pool_wei=(
            record.bull_amount_wei
            if prediction.action == "bull"
            else record.bear_amount_wei
        ),
        opposing_pool_wei=(
            record.bear_amount_wei
            if prediction.action == "bull"
            else record.bull_amount_wei
        ),
        stake_wei=stake_wei,
        fee_bps=fee_bps,
        bet_gas_wei=bet_gas_wei,
        claim_gas_wei=claim_gas_wei,
    )
    gross = gross_payout_if_win_wei(quote)
    return gross - stake_wei - bet_gas_wei - claim_gas_wei


def _settlement_for(
    market: str,
    prediction: ResearchPredictionRecord,
    record: RoundRecord,
) -> ShadowSettlementRecord:
    _validate_round_integrity(prediction, record)
    assert record.end_timestamp is not None
    return ShadowSettlementRecord(
        market=market,
        epoch=record.epoch,
        settled_timestamp_ms=record.end_timestamp * 1_000,
        outcome=record.label,
        result_source_digest=_round_source_digest(market, record),
        realized_pnl_wei=_realized_shadow_pnl(prediction, record),
        metadata={
            "source": "canonical-replay",
            "end_block": record.end_block,
            "lock_price": record.lock_price,
            "close_price": record.close_price,
            "final_bull_wei": record.bull_amount_wei,
            "final_bear_wei": record.bear_amount_wei,
            "total_amount_wei": record.total_amount_wei,
        },
    )


def _ledger_records(
    store: ShadowLedgerStore,
) -> tuple[
    dict[tuple[str, int], ResearchPredictionRecord],
    dict[tuple[str, int], ShadowSettlementRecord],
]:
    predictions: dict[tuple[str, int], ResearchPredictionRecord] = {}
    settlements: dict[tuple[str, int], ShadowSettlementRecord] = {}
    for event in store.events():
        raw_record = event.payload.get("record")
        if not isinstance(raw_record, dict):
            raise ValueError(
                f"shadow ledger event {event.sequence} record payload is invalid"
            )
        if event.kind == "prediction":
            prediction = prediction_from_payload(raw_record)
            key = (prediction.market, prediction.epoch)
            if key in predictions:
                raise ValueError(f"duplicate shadow prediction identity: {key}")
            predictions[key] = prediction
        elif event.kind == "settlement":
            settlement = settlement_from_payload(raw_record)
            key = (settlement.market, settlement.epoch)
            if key in settlements:
                raise ValueError(f"duplicate shadow settlement identity: {key}")
            settlements[key] = settlement
        else:
            raise ValueError(f"unsupported shadow ledger event kind: {event.kind}")
    return predictions, settlements


def reconcile_shadow_settlements(
    store: ShadowLedgerStore,
    replay: ReplaySnapshot,
) -> ShadowReconciliationReport:
    predictions, settlements = _ledger_records(store)
    foreign_markets = sorted(
        {market for market, _epoch in predictions if market != replay.market}
    )
    if foreign_markets:
        raise ValueError(
            "shadow ledger contains predictions outside replay market: "
            + ", ".join(foreign_markets)
        )

    rounds: dict[int, RoundRecord] = {}
    for record in replay.rounds:
        if record.epoch in rounds:
            raise ValueError(f"duplicate replay epoch {record.epoch}")
        rounds[record.epoch] = record

    pending: list[ShadowSettlementRecord] = []
    unresolved = 0
    for key, prediction in sorted(predictions.items()):
        if key in settlements:
            continue
        record = rounds.get(prediction.epoch)
        if (
            record is None
            or record.end_timestamp is None
            or record.end_block is None
            or record.label not in {"bull", "bear", "tie"}
        ):
            unresolved += 1
            continue
        pending.append(_settlement_for(replay.market, prediction, record))

    # Validate every derived settlement before mutating the append-only ledger.
    appended = tuple(store.append_settlement(record) for record in pending)
    return ShadowReconciliationReport(
        market=replay.market,
        prediction_count=len(predictions),
        existing_settlement_count=len(settlements),
        appended_settlement_count=len(appended),
        unresolved_count=unresolved,
        appended_events=appended,
    )
