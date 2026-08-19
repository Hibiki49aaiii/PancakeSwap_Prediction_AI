from __future__ import annotations

import pytest

from pancake_prediction_ai.economics import Outcome, Side
from pancake_prediction_ai.shadow import ShadowDecision, ShadowLedger


def decision(round_id: int, *, p_bull: float, side: Side, cutoff: int, p_tie: float = 0.01) -> ShadowDecision:
    return ShadowDecision(
        round_id=round_id,
        decision_cutoff_ns=cutoff,
        probability_bull=p_bull,
        probability_tie=p_tie,
        side=side,
        stake_wei=10,
        snapshot_bull_wei=100,
        snapshot_bear_wei=100,
        treasury_fee_ppm=0,
        gas_cost_wei=0,
        expected_pnl_wei=2.0,
        model_version="test-model-v1",
    )


def test_shadow_ledger_separates_decision_from_settlement(tmp_path) -> None:
    with ShadowLedger(tmp_path / "shadow.sqlite") as ledger:
        ledger.record_decision(decision(1, p_bull=0.8, side=Side.BULL, cutoff=100))
        settlement = ledger.resolve_round(
            round_id=1,
            outcome=Outcome.BULL,
            final_bull_wei=100,
            final_bear_wei=100,
            settled_at_ns=200,
        )
        assert settlement.simulated_pnl_wei > 0
        summary = ledger.summary()
        assert summary.resolved_rounds == 1
        assert summary.brier_score < 0.03
        assert summary.directional_accuracy == 1.0
        assert summary.tie_rate == 0.0


def test_tie_is_house_win_for_shadow_position(tmp_path) -> None:
    with ShadowLedger(tmp_path / "shadow.sqlite") as ledger:
        ledger.record_decision(decision(1, p_bull=0.49, p_tie=0.02, side=Side.BULL, cutoff=100))
        settlement = ledger.resolve_round(
            round_id=1,
            outcome=Outcome.TIE,
            final_bull_wei=100,
            final_bear_wei=100,
            settled_at_ns=200,
        )
        assert settlement.simulated_pnl_wei == -10
        summary = ledger.summary()
        assert summary.tie_rate == 1.0
        assert summary.directional_accuracy == 0.0


def test_shadow_duplicate_decision_and_settlement_are_rejected(tmp_path) -> None:
    with ShadowLedger(tmp_path / "shadow.sqlite") as ledger:
        item = decision(1, p_bull=0.8, side=Side.BULL, cutoff=100)
        ledger.record_decision(item)
        with pytest.raises(ValueError, match="already recorded"):
            ledger.record_decision(item)
        ledger.resolve_round(
            round_id=1,
            outcome=Outcome.BULL,
            final_bull_wei=100,
            final_bear_wei=100,
            settled_at_ns=200,
        )
        with pytest.raises(ValueError, match="already settled"):
            ledger.resolve_round(
                round_id=1,
                outcome=Outcome.BULL,
                final_bull_wei=100,
                final_bear_wei=100,
                settled_at_ns=201,
            )


def test_settlement_must_be_after_decision_cutoff(tmp_path) -> None:
    with ShadowLedger(tmp_path / "shadow.sqlite") as ledger:
        ledger.record_decision(decision(1, p_bull=0.8, side=Side.BULL, cutoff=100))
        with pytest.raises(ValueError, match="after decision cutoff"):
            ledger.resolve_round(
                round_id=1,
                outcome=Outcome.BULL,
                final_bull_wei=100,
                final_bear_wei=100,
                settled_at_ns=100,
            )
