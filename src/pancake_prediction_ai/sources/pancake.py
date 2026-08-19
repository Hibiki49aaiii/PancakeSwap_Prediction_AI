from __future__ import annotations

from ..event_store import EventRecord
from ..pancake_contract import PredictionRoundState, onchain_treasury_fee_to_ppm


def _address(value: str, field: str) -> str:
    normalized = value.lower()
    if not normalized.startswith("0x") or len(normalized) != 42:
        raise ValueError(f"{field} must be a 20-byte hex address")
    try:
        int(normalized[2:], 16)
    except ValueError as exc:
        raise ValueError(f"{field} must be hex") from exc
    return normalized


def normalize_round_snapshot(
    round_state: PredictionRoundState,
    *,
    contract_address: str,
    treasury_fee_units: int,
    block_number: int,
    block_timestamp_s: int,
    observed_at_ns: int,
) -> EventRecord:
    """Normalize one read-only `rounds(epoch)` observation.

    A contract view call does not itself carry an event timestamp. Callers must
    pair the state read with the block number/header used for that read. The
    canonical source event time is therefore the observed block timestamp,
    while `observed_at_ns` remains local arrival time.
    """

    round_state.validate()
    address = _address(contract_address, "contract_address")
    if block_number < 0:
        raise ValueError("block_number must be non-negative")
    if block_timestamp_s <= 0:
        raise ValueError("block_timestamp_s must be positive")
    if observed_at_ns < 0:
        raise ValueError("observed_at_ns must be non-negative")
    treasury_fee_ppm = onchain_treasury_fee_to_ppm(treasury_fee_units)

    return EventRecord(
        event_id=(
            f"pancake:prediction:round_snapshot:{address}:"
            f"{round_state.epoch}:{block_number}"
        ),
        source="pancake_prediction",
        topic="prediction.round_snapshot",
        event_time_ns=block_timestamp_s * 1_000_000_000,
        observed_at_ns=observed_at_ns,
        payload={
            "contract_address": address,
            "block_number": block_number,
            "block_timestamp_s": block_timestamp_s,
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
            "treasury_fee_units": treasury_fee_units,
            "treasury_fee_ppm": treasury_fee_ppm,
        },
    )


def normalize_oracle_reference(
    oracle_address: str,
    *,
    contract_address: str,
    block_number: int,
    block_timestamp_s: int,
    observed_at_ns: int,
) -> EventRecord:
    """Normalize the Prediction contract's public `oracle()` reference.

    This permits the active Chainlink feed to be discovered from the protocol
    contract rather than maintained as a separate hard-coded feed address.
    """

    oracle = _address(oracle_address, "oracle_address")
    contract = _address(contract_address, "contract_address")
    if block_number < 0 or block_timestamp_s <= 0 or observed_at_ns < 0:
        raise ValueError("block/observation timestamps are invalid")
    return EventRecord(
        event_id=f"pancake:prediction:oracle:{contract}:{block_number}",
        source="pancake_prediction",
        topic="prediction.oracle_reference",
        event_time_ns=block_timestamp_s * 1_000_000_000,
        observed_at_ns=observed_at_ns,
        payload={
            "contract_address": contract,
            "oracle_address": oracle,
            "block_number": block_number,
            "block_timestamp_s": block_timestamp_s,
        },
    )
