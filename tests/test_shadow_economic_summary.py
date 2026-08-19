from __future__ import annotations

import pytest

from pancake_prediction_ai.event_store import EventRecord, EventStore
from pancake_prediction_ai.shadow_economic_summary import summarize_shadow_economics


def _decision(round_id: int, action: str, side: str | None, expected_return: float | None) -> EventRecord:
    bull_ev = {"expected_return_on_stake": expected_return if side == "BULL" else -0.1}
    bear_ev = {"expected_return_on_stake": expected_return if side == "BEAR" else -0.1}
    return EventRecord(
        event_id=f"decision:{round_id}",
        source="shadow",
        topic="shadow.economic_decision",
        event_time_ns=round_id,
        observed_at_ns=round_id,
        payload={
            "round_id": round_id,
            "action": action,
            "selected_side": side,
            "bull_ev": bull_ev,
            "bear_ev": bear_ev,
        },
    )


def _settlement(
    round_id: int,
    *,
    action: str,
    side: str | None,
    resolution: str,
    pnl: int,
    adjusted: float,
) -> EventRecord:
    return EventRecord(
        event_id=f"settlement:{round_id}",
        source="shadow",
        topic="shadow.economic_settlement",
        event_time_ns=round_id + 100,
        observed_at_ns=round_id + 100,
        payload={
            "round_id": round_id,
            "action": action,
            "selected_side": side,
            "resolution": resolution,
            "pnl_if_executed_wei": pnl,
            "probability_adjusted_pnl_wei": adjusted,
            "claim_or_refund_gas_modeled": False,
        },
    )


def test_shadow_economic_summary_keeps_conditional_and_assumption_adjusted_pnl_separate(tmp_path) -> None:
    with EventStore(tmp_path / "observed.sqlite") as store:
        store.append_many(
            (
                _decision(1, "BET", "BULL", 0.2),
                _decision(2, "ABSTAIN", None, None),
                _decision(3, "BET", "BEAR", 0.1),
                _decision(4, "BET", "BULL", 0.3),
                _settlement(1, action="BET", side="BULL", resolution="BULL", pnl=100, adjusted=50.0),
                _settlement(2, action="ABSTAIN", side=None, resolution="BEAR", pnl=0, adjusted=0.0),
                _settlement(3, action="BET", side="BEAR", resolution="TIE", pnl=-60, adjusted=-30.0),
            )
        )
        summary = summarize_shadow_economics(store)

    assert summary.decision_rounds == 4
    assert summary.bet_decisions == 3
    assert summary.abstentions == 1
    assert summary.settled_rounds == 3
    assert summary.unresolved_rounds == 1
    assert summary.settled_bets == 2
    assert summary.winning_bets == 1
    assert summary.losing_bets == 1
    assert summary.tie_losses == 1
    assert summary.refunds == 0
    assert summary.conditional_net_pnl_wei == 40
    assert summary.conditional_max_drawdown_wei == 60
    assert summary.probability_adjusted_net_pnl_wei == 20.0
    assert summary.probability_adjusted_max_drawdown_wei == 30.0
    assert summary.average_selected_expected_return == pytest.approx(0.2)
    assert summary.claim_or_refund_gas_fully_modeled is False
