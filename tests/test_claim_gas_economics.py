from __future__ import annotations

import pytest

from pancake_prediction_ai.economics import ExecutionCost, PoolState, Side, evaluate_bet_ev
from pancake_prediction_ai.event_store import EventRecord, EventStore
from pancake_prediction_ai.pancake_contract import PredictionRoundState
from pancake_prediction_ai.rpc_snapshot import BlockAnchor
from pancake_prediction_ai.shadow_settlement import (
    ShadowRoundResolution,
    reconcile_shadow_economic_round,
)


WEI = 10**18


class FakeClient:
    def chain_id(self) -> int:
        return 56


def test_claim_cost_is_charged_only_on_win_in_ev() -> None:
    pool = PoolState(bull_wei=100, bear_wei=300, treasury_fee_ppm=20_000)
    without_claim = evaluate_bet_ev(
        probability_bull=0.8,
        probability_tie=0.001,
        side=Side.BULL,
        stake_wei=10,
        pool=pool,
        cost=ExecutionCost(gas_cost_wei=1),
    )
    with_claim = evaluate_bet_ev(
        probability_bull=0.8,
        probability_tie=0.001,
        side=Side.BULL,
        stake_wei=10,
        pool=pool,
        cost=ExecutionCost(gas_cost_wei=1, claim_cost_if_win_wei=3),
    )
    assert with_claim.pnl_if_win_wei == without_claim.pnl_if_win_wei - 3
    assert with_claim.pnl_if_lose_wei == without_claim.pnl_if_lose_wei
    assert with_claim.expected_pnl_if_executed_wei == pytest.approx(
        without_claim.expected_pnl_if_executed_wei - 0.8 * 3
    )


def _decision(store: EventStore, *, claim_gas: int | None) -> None:
    store.append(
        EventRecord(
            event_id="shadow:economic_decision:7",
            source="shadow",
            topic="shadow.economic_decision",
            event_time_ns=100,
            observed_at_ns=100,
            payload={
                "round_id": 7,
                "action": "BET",
                "selected_side": "BULL",
                "stake_wei": 10 * WEI,
                "pool": {
                    "bull_wei": 100 * WEI,
                    "bear_wei": 300 * WEI,
                    "treasury_fee_ppm": 20_000,
                },
                "assumed_execution": {
                    "gas_cost_wei": 10**15,
                    "claim_or_refund_gas_cost_wei": claim_gas,
                    "same_side_inflow_wei": 0,
                    "opposite_side_inflow_wei": 0,
                    "execution_success_probability": 1.0,
                    "min_expected_return": 0.0,
                },
            },
        )
    )


def _round(*, oracle_called: bool = True) -> PredictionRoundState:
    bull = 100 * WEI
    bear = 300 * WEI
    total = bull + bear
    fee_units = 200
    reward = total - (total * fee_units) // 10_000 if oracle_called else 0
    return PredictionRoundState(
        epoch=7,
        start_timestamp=1_000,
        lock_timestamp=1_200,
        close_timestamp=1_500,
        lock_price=100,
        close_price=101 if oracle_called else 0,
        lock_oracle_id=1,
        close_oracle_id=2 if oracle_called else 0,
        total_amount_wei=total,
        bull_amount_wei=bull,
        bear_amount_wei=bear,
        reward_base_cal_amount_wei=bull if oracle_called else 0,
        reward_amount_wei=reward,
        oracle_called=oracle_called,
    )


def _anchor(timestamp_s: int = 2_000) -> BlockAnchor:
    return BlockAnchor(123, "0x" + "ab" * 32, timestamp_s)


def test_winning_settlement_subtracts_decision_time_claim_gas_once(tmp_path) -> None:
    claim_gas = 2 * 10**15
    with EventStore(tmp_path / "observed.sqlite") as store:
        _decision(store, claim_gas=claim_gas)
        state = _round()
        result = reconcile_shadow_economic_round(
            store,
            FakeClient(),  # type: ignore[arg-type]
            round_id=7,
            anchor_fetcher=lambda client: _anchor(),
            round_reader=lambda client, **kwargs: state,
            buffer_reader=lambda client, **kwargs: 30,
            clock_ns=lambda: 3_000_000_000_000,
        )
        stake = 10 * WEI
        entry_gas = 10**15
        simulated_total = state.total_amount_wei + stake
        distributable = simulated_total - (simulated_total * 200) // 10_000
        gross = (stake * distributable) // (state.bull_amount_wei + stake)
        assert result.pnl_if_executed_wei == gross - stake - entry_gas - claim_gas
        payload = result.stored_event.event.payload  # type: ignore[union-attr]
        assert payload["claim_or_refund_gas_cost_wei"] == claim_gas
        assert payload["claim_or_refund_gas_applied_wei"] == claim_gas
        assert payload["claim_or_refund_gas_modeled"] is True


def test_refund_settlement_subtracts_configured_refund_gas(tmp_path) -> None:
    refund_gas = 3 * 10**15
    with EventStore(tmp_path / "observed.sqlite") as store:
        _decision(store, claim_gas=refund_gas)
        state = _round(oracle_called=False)
        result = reconcile_shadow_economic_round(
            store,
            FakeClient(),  # type: ignore[arg-type]
            round_id=7,
            anchor_fetcher=lambda client: _anchor(timestamp_s=1_531),
            round_reader=lambda client, **kwargs: state,
            buffer_reader=lambda client, **kwargs: 30,
            clock_ns=lambda: 3_000_000_000_000,
        )
        assert result.resolution is ShadowRoundResolution.REFUND
        assert result.pnl_if_executed_wei == -(10**15) - refund_gas
        payload = result.stored_event.event.payload  # type: ignore[union-attr]
        assert payload["claim_or_refund_gas_modeled"] is True


def test_missing_claim_cost_keeps_winning_round_explicitly_incomplete(tmp_path) -> None:
    with EventStore(tmp_path / "observed.sqlite") as store:
        _decision(store, claim_gas=None)
        state = _round()
        result = reconcile_shadow_economic_round(
            store,
            FakeClient(),  # type: ignore[arg-type]
            round_id=7,
            anchor_fetcher=lambda client: _anchor(),
            round_reader=lambda client, **kwargs: state,
            buffer_reader=lambda client, **kwargs: 30,
            clock_ns=lambda: 3_000_000_000_000,
        )
        payload = result.stored_event.event.payload  # type: ignore[union-attr]
        assert payload["claim_or_refund_gas_applied_wei"] == 0
        assert payload["claim_or_refund_gas_modeled"] is False
