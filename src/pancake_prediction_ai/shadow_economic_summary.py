from __future__ import annotations

from dataclasses import dataclass

from .event_store import EventStore
from .shadow_economics import ShadowEconomicAction
from .shadow_settlement import ShadowRoundResolution


@dataclass(frozen=True, slots=True)
class ShadowEconomicSummary:
    decision_rounds: int
    bet_decisions: int
    abstentions: int
    settled_rounds: int
    unresolved_rounds: int
    settled_bets: int
    winning_bets: int
    losing_bets: int
    tie_losses: int
    refunds: int
    conditional_net_pnl_wei: int
    conditional_max_drawdown_wei: int
    probability_adjusted_net_pnl_wei: float
    probability_adjusted_max_drawdown_wei: float
    average_selected_expected_return: float | None
    claim_or_refund_gas_fully_modeled: bool


def _selected_expected_return(payload: dict[str, object]) -> float | None:
    side = payload.get("selected_side")
    if side is None:
        return None
    key = "bull_ev" if side == "BULL" else "bear_ev"
    raw = payload.get(key)
    if not isinstance(raw, dict):
        raise ValueError("shadow economic EV payload is invalid")
    value = raw.get("expected_return_on_stake")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("shadow expected_return_on_stake is invalid")
    return float(value)


def summarize_shadow_economics(store: EventStore) -> ShadowEconomicSummary:
    """Summarize paper economics without upgrading assumptions to observations."""

    if store.mode != "observed":
        raise ValueError("shadow economic summary requires observed Event Store")
    decisions: dict[int, dict[str, object]] = {}
    settlements: dict[int, dict[str, object]] = {}
    for stored in store.read_all_ingest_order():
        event = stored.event
        if event.source != "shadow":
            continue
        if event.topic not in {"shadow.economic_decision", "shadow.economic_settlement"}:
            continue
        round_id = event.payload.get("round_id")
        if isinstance(round_id, bool) or not isinstance(round_id, int):
            raise ValueError("shadow economic round_id is invalid")
        target = decisions if event.topic == "shadow.economic_decision" else settlements
        if round_id in target:
            raise ValueError(f"duplicate {event.topic} for round {round_id}")
        target[round_id] = dict(event.payload)

    bet_decisions = 0
    abstentions = 0
    expected_returns: list[float] = []
    for payload in decisions.values():
        action = ShadowEconomicAction(str(payload.get("action")))
        if action is ShadowEconomicAction.BET:
            bet_decisions += 1
            expected = _selected_expected_return(payload)
            if expected is None:
                raise ValueError("BET decision is missing selected expected return")
            expected_returns.append(expected)
        else:
            abstentions += 1

    winning_bets = 0
    losing_bets = 0
    tie_losses = 0
    refunds = 0
    settled_bets = 0
    conditional_equity = 0
    conditional_peak = 0
    conditional_max_drawdown = 0
    adjusted_equity = 0.0
    adjusted_peak = 0.0
    adjusted_max_drawdown = 0.0
    claim_or_refund_gas_fully_modeled = True

    for round_id in sorted(settlements):
        payload = settlements[round_id]
        if round_id not in decisions:
            raise ValueError(f"settlement without economic decision for round {round_id}")
        action = ShadowEconomicAction(str(payload.get("action")))
        try:
            resolution = ShadowRoundResolution(str(payload.get("resolution")))
        except ValueError as exc:
            raise ValueError("shadow settlement resolution is invalid") from exc
        pnl_raw = payload.get("pnl_if_executed_wei")
        adjusted_raw = payload.get("probability_adjusted_pnl_wei")
        if isinstance(pnl_raw, bool) or not isinstance(pnl_raw, int):
            raise ValueError("shadow settlement pnl_if_executed_wei is invalid")
        if isinstance(adjusted_raw, bool) or not isinstance(adjusted_raw, (int, float)):
            raise ValueError("shadow settlement probability_adjusted_pnl_wei is invalid")
        if payload.get("claim_or_refund_gas_modeled") is not True:
            claim_or_refund_gas_fully_modeled = False

        conditional_equity += pnl_raw
        conditional_peak = max(conditional_peak, conditional_equity)
        conditional_max_drawdown = max(
            conditional_max_drawdown,
            conditional_peak - conditional_equity,
        )
        adjusted_equity += float(adjusted_raw)
        adjusted_peak = max(adjusted_peak, adjusted_equity)
        adjusted_max_drawdown = max(
            adjusted_max_drawdown,
            adjusted_peak - adjusted_equity,
        )

        if action is not ShadowEconomicAction.BET:
            continue
        settled_bets += 1
        if resolution is ShadowRoundResolution.REFUND:
            refunds += 1
            continue
        side = payload.get("selected_side")
        if side not in {"BULL", "BEAR"}:
            raise ValueError("settled BET is missing selected_side")
        if resolution is ShadowRoundResolution.TIE:
            tie_losses += 1
            losing_bets += 1
        elif side == resolution.value:
            winning_bets += 1
        else:
            losing_bets += 1

    unresolved = set(decisions) - set(settlements)
    average_expected = (
        None if not expected_returns else sum(expected_returns) / len(expected_returns)
    )
    return ShadowEconomicSummary(
        decision_rounds=len(decisions),
        bet_decisions=bet_decisions,
        abstentions=abstentions,
        settled_rounds=len(settlements),
        unresolved_rounds=len(unresolved),
        settled_bets=settled_bets,
        winning_bets=winning_bets,
        losing_bets=losing_bets,
        tie_losses=tie_losses,
        refunds=refunds,
        conditional_net_pnl_wei=conditional_equity,
        conditional_max_drawdown_wei=conditional_max_drawdown,
        probability_adjusted_net_pnl_wei=adjusted_equity,
        probability_adjusted_max_drawdown_wei=adjusted_max_drawdown,
        average_selected_expected_return=average_expected,
        claim_or_refund_gas_fully_modeled=claim_or_refund_gas_fully_modeled,
    )
