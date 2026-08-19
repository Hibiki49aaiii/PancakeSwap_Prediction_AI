from __future__ import annotations

from types import SimpleNamespace

import pytest

from pancake_prediction_ai.binance_ingest import BinancePollResult
from pancake_prediction_ai.event_store import EventRecord, EventStore
from pancake_prediction_ai.observed_cycle import run_observed_shadow_cycle
from pancake_prediction_ai.shadow_economics import ShadowEconomicPolicy


class FakeArtifact:
    artifact_sha256 = "a" * 64

    def validate(self) -> None:
        return None


def event(event_id: str, source: str, topic: str, observed_at_ns: int) -> EventRecord:
    return EventRecord(
        event_id=event_id,
        source=source,
        topic=topic,
        event_time_ns=observed_at_ns,
        observed_at_ns=observed_at_ns,
        payload={"value": event_id},
    )


def test_cycle_persists_observations_before_inference_and_returns_tip(tmp_path) -> None:
    calls: list[str] = []
    store = EventStore(tmp_path / "observed.sqlite", mode="observed")

    def collect_binance(client, target_store, **kwargs):
        calls.append("binance")
        target_store.append(event("b1", "binance_spot", "market.book_ticker", 10))
        return BinancePollResult(0, None, "b1")

    def collect_protocol(client, target_store, **kwargs):
        calls.append("protocol")
        stored = target_store.append(event("p1", "pancake_prediction", "prediction.round_snapshot", 20))
        return SimpleNamespace(snapshot=SimpleNamespace(current_epoch=123), stored_events=(stored,))

    def infer(artifact, target_store, **kwargs):
        calls.append("inference")
        assert [item.event.event_id for item in target_store.read_all_ingest_order()] == ["b1", "p1"]
        target_store.append(event("s1", "shadow", "shadow.model_decision", 30))
        return SimpleNamespace(accepted=True, blockers=(), predicted_outcome="BULL")

    result = run_observed_shadow_cycle(
        store,
        FakeArtifact(),
        binance_client=object(),
        rpc_client=object(),
        binance_collector=collect_binance,
        protocol_collector=collect_protocol,
        inference_runner=infer,
        clock_ns=lambda: 25,
    )

    assert calls == ["binance", "protocol", "inference"]
    assert result.economic is None
    assert result.store_event_count == 3
    assert result.store_tip_hash == store.read_all_ingest_order()[-1].event_hash
    assert store.verify_chain()
    store.close()


def test_cycle_can_append_simulated_economic_decision_after_accepted_inference(tmp_path) -> None:
    calls: list[str] = []
    store = EventStore(tmp_path / "observed-economics.sqlite", mode="observed")

    def collect_binance(client, target_store, **kwargs):
        calls.append("binance")
        target_store.append(event("b1", "binance_spot", "market.book_ticker", 10))
        return BinancePollResult(0, None, "b1")

    def collect_protocol(client, target_store, **kwargs):
        calls.append("protocol")
        stored = target_store.append(event("p1", "pancake_prediction", "prediction.round_snapshot", 20))
        return SimpleNamespace(snapshot=SimpleNamespace(current_epoch=123), stored_events=(stored,))

    def infer(artifact, target_store, **kwargs):
        calls.append("inference")
        target_store.append(event("s1", "shadow", "shadow.model_decision", 30))
        return SimpleNamespace(accepted=True, blockers=(), predicted_outcome="BULL")

    def economic(inference, target_store, *, policy, **kwargs):
        calls.append("economics")
        assert policy.stake_wei == 10
        stored = target_store.append(event("e1", "shadow", "shadow.economic_decision", 40))
        return SimpleNamespace(action=SimpleNamespace(value="BET"), selected_side=SimpleNamespace(value="BULL"), stored_event=stored)

    result = run_observed_shadow_cycle(
        store,
        FakeArtifact(),
        binance_client=object(),
        rpc_client=object(),
        economic_policy=ShadowEconomicPolicy(stake_wei=10),
        binance_collector=collect_binance,
        protocol_collector=collect_protocol,
        inference_runner=infer,
        economic_runner=economic,
        clock_ns=lambda: 25,
    )

    assert calls == ["binance", "protocol", "inference", "economics"]
    assert result.economic is not None
    assert result.economic.stored_event.event.event_id == "e1"
    assert result.store_event_count == 4
    assert result.store_tip_hash == store.read_all_ingest_order()[-1].event_hash
    assert store.verify_chain()
    store.close()


def test_cycle_does_not_run_economics_when_inference_is_blocked(tmp_path) -> None:
    calls: list[str] = []
    store = EventStore(tmp_path / "observed-blocked.sqlite", mode="observed")

    def collect_binance(client, target_store, **kwargs):
        target_store.append(event("b1", "binance_spot", "market.book_ticker", 10))
        return BinancePollResult(0, None, "b1")

    def collect_protocol(client, target_store, **kwargs):
        stored = target_store.append(event("p1", "pancake_prediction", "prediction.round_snapshot", 20))
        return SimpleNamespace(snapshot=SimpleNamespace(current_epoch=123), stored_events=(stored,))

    def infer(artifact, target_store, **kwargs):
        return SimpleNamespace(accepted=False, blockers=("quality",), predicted_outcome=None)

    def economic(*args, **kwargs):
        calls.append("economics")
        raise AssertionError("economic runner must not be called")

    result = run_observed_shadow_cycle(
        store,
        FakeArtifact(),
        binance_client=object(),
        rpc_client=object(),
        economic_policy=ShadowEconomicPolicy(stake_wei=10),
        binance_collector=collect_binance,
        protocol_collector=collect_protocol,
        inference_runner=infer,
        economic_runner=economic,
    )

    assert calls == []
    assert result.economic is None
    store.close()


def test_cycle_rejects_reconstructed_store_before_collection(tmp_path) -> None:
    store = EventStore(tmp_path / "historical.sqlite", mode="reconstructed")
    with pytest.raises(ValueError, match="observed Event Store"):
        run_observed_shadow_cycle(
            store,
            FakeArtifact(),
            binance_client=object(),
            rpc_client=object(),
        )
    assert store.read_all_ingest_order() == ()
    store.close()


@pytest.mark.parametrize("trade_limit", [0, 1001])
def test_cycle_rejects_invalid_trade_limit(tmp_path, trade_limit: int) -> None:
    store = EventStore(tmp_path / f"invalid-{trade_limit}.sqlite", mode="observed")
    with pytest.raises(ValueError, match="trade_limit"):
        run_observed_shadow_cycle(
            store,
            FakeArtifact(),
            binance_client=object(),
            rpc_client=object(),
            trade_limit=trade_limit,
        )
    store.close()
