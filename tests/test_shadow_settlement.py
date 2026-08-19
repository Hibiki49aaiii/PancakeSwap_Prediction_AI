from __future__ import annotations

from pancake_prediction_ai.event_store import EventRecord, EventStore
from pancake_prediction_ai.pancake_contract import PredictionRoundState
from pancake_prediction_ai.rpc_snapshot import BlockAnchor
from pancake_prediction_ai.shadow_settlement import (
    ShadowRoundResolution,
    ShadowSettlementStatus,
    reconcile_shadow_economic_round,
)


WEI = 10**18


class FakeClient:
    def chain_id(self) -> int:
        return 56


def _decision(
    store: EventStore,
    *,
    action: str = "BET",
    side: str | None = "BULL",
    stake: int = 10 * WEI,
    gas: int = 10**15,
    p_exec: float = 0.5,
) -> None:
    store.append(
        EventRecord(
            event_id="shadow:economic_decision:7",
            source="shadow",
            topic="shadow.economic_decision",
            event_time_ns=100,
            observed_at_ns=100,
            payload={
                "round_id": 7,
                "action": action,
                "selected_side": side,
                "stake_wei": stake,
                "pool": {
                    "bull_wei": 100 * WEI,
                    "bear_wei": 300 * WEI,
                    "treasury_fee_ppm": 20_000,
                },
                "assumed_execution": {
                    "gas_cost_wei": gas,
                    "same_side_inflow_wei": 0,
                    "opposite_side_inflow_wei": 0,
                    "execution_success_probability": p_exec,
                    "min_expected_return": 0.0,
                },
            },
        )
    )


def _round(
    *,
    close_price: int = 101,
    lock_price: int = 100,
    oracle_called: bool = True,
    close_timestamp: int = 1_500,
) -> PredictionRoundState:
    bull = 100 * WEI
    bear = 300 * WEI
    total = bull + bear
    fee_units = 200
    if oracle_called and close_price > lock_price:
        reward_base = bull
        reward = total - (total * fee_units) // 10_000
    elif oracle_called and close_price < lock_price:
        reward_base = bear
        reward = total - (total * fee_units) // 10_000
    else:
        reward_base = 0
        reward = 0
    return PredictionRoundState(
        epoch=7,
        start_timestamp=1_000,
        lock_timestamp=1_200,
        close_timestamp=close_timestamp,
        lock_price=lock_price,
        close_price=close_price,
        lock_oracle_id=1,
        close_oracle_id=2 if oracle_called else 0,
        total_amount_wei=total,
        bull_amount_wei=bull,
        bear_amount_wei=bear,
        reward_base_cal_amount_wei=reward_base,
        reward_amount_wei=reward,
        oracle_called=oracle_called,
    )


def _anchor(timestamp_s: int = 2_000) -> BlockAnchor:
    return BlockAnchor(
        number=123,
        block_hash="0x" + "ab" * 32,
        timestamp_s=timestamp_s,
    )


def test_winning_shadow_bet_uses_observed_final_pool_and_inferred_settlement_fee(tmp_path) -> None:
    with EventStore(tmp_path / "observed.sqlite") as store:
        _decision(store)
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
        gas = 10**15
        simulated_total = state.total_amount_wei + stake
        distributable = simulated_total - (simulated_total * 200) // 10_000
        gross = (stake * distributable) // (state.bull_amount_wei + stake)
        expected_pnl = gross - stake - gas

        assert result.status is ShadowSettlementStatus.SETTLED
        assert result.resolution is ShadowRoundResolution.BULL
        assert result.settled_fee_units == 200
        assert result.pnl_if_executed_wei == expected_pnl
        assert result.probability_adjusted_pnl_wei == 0.5 * expected_pnl
        events = store.read_all_ingest_order()
        assert [item.event.topic for item in events[-2:]] == [
            "prediction.settlement_snapshot",
            "shadow.economic_settlement",
        ]
        assert events[-1].prev_hash == events[-2].event_hash
        assert events[-1].event.payload["claim_or_refund_gas_modeled"] is False
        assert store.verify_chain()


def test_losing_shadow_bet_records_full_stake_and_gas_loss(tmp_path) -> None:
    with EventStore(tmp_path / "observed.sqlite") as store:
        _decision(store, side="BULL", p_exec=1.0)
        state = _round(close_price=99)
        result = reconcile_shadow_economic_round(
            store,
            FakeClient(),  # type: ignore[arg-type]
            round_id=7,
            anchor_fetcher=lambda client: _anchor(),
            round_reader=lambda client, **kwargs: state,
            buffer_reader=lambda client, **kwargs: 30,
            clock_ns=lambda: 3_000_000_000_000,
        )
        assert result.resolution is ShadowRoundResolution.BEAR
        assert result.pnl_if_executed_wei == -(10 * WEI) - 10**15


def test_tie_is_house_win_and_does_not_need_fee_inference(tmp_path) -> None:
    with EventStore(tmp_path / "observed.sqlite") as store:
        _decision(store, side="BULL", p_exec=1.0)
        state = _round(close_price=100, lock_price=100)
        result = reconcile_shadow_economic_round(
            store,
            FakeClient(),  # type: ignore[arg-type]
            round_id=7,
            anchor_fetcher=lambda client: _anchor(),
            round_reader=lambda client, **kwargs: state,
            buffer_reader=lambda client, **kwargs: 30,
            clock_ns=lambda: 3_000_000_000_000,
        )
        assert result.status is ShadowSettlementStatus.SETTLED
        assert result.resolution is ShadowRoundResolution.TIE
        assert result.settled_fee_units is None
        assert result.pnl_if_executed_wei == -(10 * WEI) - 10**15


def test_invalid_round_becomes_refund_only_after_buffer_window(tmp_path) -> None:
    with EventStore(tmp_path / "observed.sqlite") as store:
        _decision(store, side="BULL", p_exec=1.0)
        state = _round(oracle_called=False, close_timestamp=1_500)
        pending = reconcile_shadow_economic_round(
            store,
            FakeClient(),  # type: ignore[arg-type]
            round_id=7,
            anchor_fetcher=lambda client: _anchor(timestamp_s=1_520),
            round_reader=lambda client, **kwargs: state,
            buffer_reader=lambda client, **kwargs: 30,
        )
        assert pending.status is ShadowSettlementStatus.PENDING
        assert pending.blockers == ("refund_window_not_open",)
        assert len(store.read_all_ingest_order()) == 1

        settled = reconcile_shadow_economic_round(
            store,
            FakeClient(),  # type: ignore[arg-type]
            round_id=7,
            anchor_fetcher=lambda client: _anchor(timestamp_s=1_531),
            round_reader=lambda client, **kwargs: state,
            buffer_reader=lambda client, **kwargs: 30,
            clock_ns=lambda: 3_000_000_000_000,
        )
        assert settled.status is ShadowSettlementStatus.SETTLED
        assert settled.resolution is ShadowRoundResolution.REFUND
        assert settled.pnl_if_executed_wei == -(10**15)


def test_abstain_settles_to_zero_pnl_while_preserving_observed_outcome(tmp_path) -> None:
    with EventStore(tmp_path / "observed.sqlite") as store:
        _decision(store, action="ABSTAIN", side=None)
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
        assert result.resolution is ShadowRoundResolution.BULL
        assert result.pnl_if_executed_wei == 0
        assert result.probability_adjusted_pnl_wei == 0.0


def test_reward_mismatch_is_anomaly_and_not_persisted_as_settlement(tmp_path) -> None:
    with EventStore(tmp_path / "observed.sqlite") as store:
        _decision(store)
        state = _round()
        invalid = PredictionRoundState(
            **{
                **state.__dict__,
                "reward_amount_wei": state.total_amount_wei + 1,
            }
        )
        result = reconcile_shadow_economic_round(
            store,
            FakeClient(),  # type: ignore[arg-type]
            round_id=7,
            anchor_fetcher=lambda client: _anchor(),
            round_reader=lambda client, **kwargs: invalid,
            buffer_reader=lambda client, **kwargs: 30,
        )
        assert result.status is ShadowSettlementStatus.ANOMALY
        assert result.blockers[0].startswith("settlement_fee_not_uniquely_inferable")
        assert len(store.read_all_ingest_order()) == 1


def test_settlement_is_idempotent_after_first_success(tmp_path) -> None:
    calls = 0
    with EventStore(tmp_path / "observed.sqlite") as store:
        _decision(store)
        state = _round()

        def fetch(client):
            nonlocal calls
            calls += 1
            return _anchor()

        first = reconcile_shadow_economic_round(
            store,
            FakeClient(),  # type: ignore[arg-type]
            round_id=7,
            anchor_fetcher=fetch,
            round_reader=lambda client, **kwargs: state,
            buffer_reader=lambda client, **kwargs: 30,
            clock_ns=lambda: 3_000_000_000_000,
        )
        second = reconcile_shadow_economic_round(
            store,
            FakeClient(),  # type: ignore[arg-type]
            round_id=7,
            anchor_fetcher=fetch,
            round_reader=lambda client, **kwargs: state,
            buffer_reader=lambda client, **kwargs: 30,
        )
        assert first.status is ShadowSettlementStatus.SETTLED
        assert second.status is ShadowSettlementStatus.ALREADY_SETTLED
        assert calls == 1
        assert len(store.read_all_ingest_order()) == 3
