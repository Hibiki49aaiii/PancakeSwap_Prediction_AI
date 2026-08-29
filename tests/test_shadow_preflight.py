from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from pancake_prediction import shadow_preflight
from pancake_prediction.binance_live import (
    BinanceLiveError,
    BinanceRestPage,
    LiveVenue,
)
from pancake_prediction.clickhouse import ClickHouseError, QueryParameter
from pancake_prediction.clickhouse_schema import ClickHouseBinanceSchemaReport
from pancake_prediction.contracts import MARKETS
from pancake_prediction.rpc import RpcError
from pancake_prediction.shadow_inference import ShadowInferenceConfig
from pancake_prediction.shadow_ledger import ShadowLedgerStore
from pancake_prediction.shadow_runtime import (
    ShadowRuntimeConfig,
    build_shadow_runtime_campaign_manifest,
)


@dataclass(frozen=True)
class FakeRound:
    label: str | None
    end_timestamp: int | None


@dataclass(frozen=True)
class FakeReplay:
    rounds: tuple[FakeRound, ...]


@dataclass(frozen=True)
class FakeOracleHistory:
    events: tuple[object, ...]


@dataclass(frozen=True)
class FakeInputs:
    replay: FakeReplay
    oracle_history: FakeOracleHistory


class FakeStore:
    def __init__(self, values: Mapping[str, str]) -> None:
        self.values = dict(values)

    def metadata(self, key: str) -> str | None:
        return self.values.get(key)


@dataclass
class FakeRpc:
    chain: int = 56
    head: int = 1_000
    fail: bool = False

    def chain_id(self) -> int:
        if self.fail:
            raise RpcError("https://secret-rpc.invalid/key")
        return self.chain

    def block_number(self) -> int:
        if self.fail:
            raise RpcError("https://secret-rpc.invalid/key")
        return self.head

    def block(self, number: int) -> dict[str, object]:
        raise AssertionError(number)

    def get_logs(
        self,
        address: str,
        from_block: int,
        to_block: int,
        *,
        topic0s: tuple[str, ...] | None = None,
    ) -> list[dict[str, object]]:
        raise AssertionError((address, from_block, to_block, topic0s))

    def get_code(self, address: str, block: int | str = "latest") -> str:
        raise AssertionError((address, block))

    def eth_call(self, to: str, data: str, block: int | str = "latest") -> str:
        raise AssertionError((to, data, block))


@dataclass
class FakeClickHouse:
    spot_rows: int = 100
    perp_rows: int = 100
    fail: bool = False

    def query_json_rows(
        self,
        query: str,
        *,
        parameters: Mapping[str, QueryParameter] | None = None,
    ) -> Iterator[dict[str, object]]:
        del query
        if self.fail:
            raise ClickHouseError("clickhouse-secret-password")
        if parameters is None:
            raise AssertionError("lineage query must be parameterized")
        venue = str(parameters["venue"])
        count = self.spot_rows if venue == "spot" else self.perp_rows
        yield {
            "row_count": count,
            "first_trade_timestamp_ms": None if count == 0 else 100,
            "last_trade_timestamp_ms": None if count == 0 else 200,
        }


@dataclass
class FakeBinance:
    fail_venues: tuple[LiveVenue, ...] = ()

    def fetch_agg_trades(
        self,
        *,
        venue: LiveVenue,
        symbol: str,
        parameters: Mapping[str, int | str],
    ) -> BinanceRestPage:
        assert symbol == "BNBUSDT"
        assert parameters == {"limit": 1}
        if venue in self.fail_venues:
            raise BinanceLiveError("binance-secret-token")
        return BinanceRestPage(
            rows=({"a": 1},),
            observed_at_ms=123,
            source_sha256="a" * 64,
        )


def _config(*, include_perp: bool = True) -> ShadowRuntimeConfig:
    return ShadowRuntimeConfig(
        include_perp=include_perp,
        oracle_history_updates=3,
        oracle_hazard_min_intervals=2,
        inference=ShadowInferenceConfig(
            min_train_rounds=5,
            calibration_rounds=2,
            pool_min_train_rounds=4,
            pool_window_rounds=10,
            purge_rounds=2,
            stake_wei=100,
            bet_gas_wei=2,
            claim_gas_wei=1,
        ),
    )


def _schema(*, ready: bool = True) -> ClickHouseBinanceSchemaReport:
    if ready:
        return ClickHouseBinanceSchemaReport(
            table_exists=True,
            engine="ReplacingMergeTree",
            engine_full="ReplacingMergeTree(ingest_version)",
            sorting_key=(
                "venue,symbol,timestamp_unit,availability_lag_ms,"
                "aggregate_trade_id"
            ),
            missing_columns=(),
            incompatible_columns=(),
        )
    return ClickHouseBinanceSchemaReport(
        table_exists=False,
        engine=None,
        engine_full=None,
        sorting_key=None,
        missing_columns=("venue",),
        incompatible_columns=(),
    )


def _inputs(*, settled: int = 7, rounds: int = 10, chainlink: int = 3) -> FakeInputs:
    records = tuple(
        FakeRound(
            label=("bull" if index % 2 else "bear") if index < settled else None,
            end_timestamp=100 + index if index < settled else None,
        )
        for index in range(rounds)
    )
    return FakeInputs(
        replay=FakeReplay(records),
        oracle_history=FakeOracleHistory(tuple(object() for _ in range(chainlink))),
    )


def _metadata() -> dict[str, str]:
    return {
        "BNBUSD.last_collected_block": "900",
        "BNBUSD.oracle_proxy_anchor_address": "0x" + "1" * 40,
        "BNBUSD.oracle_anchor_address": "0x" + "2" * 40,
    }


def _patch_canonical(
    monkeypatch: pytest.MonkeyPatch,
    *,
    inputs: FakeInputs | None = None,
    metadata: Mapping[str, str] | None = None,
) -> None:
    selected_inputs = _inputs() if inputs is None else inputs
    selected_metadata = _metadata() if metadata is None else dict(metadata)
    monkeypatch.setattr(
        shadow_preflight,
        "EventStore",
        lambda path: FakeStore(selected_metadata),
    )
    monkeypatch.setattr(
        shadow_preflight,
        "load_canonical_research_inputs",
        lambda database, market: selected_inputs,
    )


def test_preflight_ready_when_structural_inputs_and_sources_are_available(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    canonical = tmp_path / "canonical.sqlite3"
    canonical.write_bytes(b"existing-canonical-db")
    _patch_canonical(monkeypatch)
    monkeypatch.setattr(
        shadow_preflight,
        "inspect_binance_trade_schema",
        lambda source: _schema(),
    )

    report = shadow_preflight.run_shadow_runtime_preflight(
        FakeRpc(),
        FakeClickHouse(),
        FakeBinance(),
        MARKETS["BNBUSD"],
        canonical,
        tmp_path / "shadow.sqlite3",
        config=_config(),
    )

    assert report.ready is True
    assert report.failures == ()
    assert report.replay_rounds == 10
    assert report.settled_labeled_rounds == 7
    assert report.minimum_settled_rounds == 7
    assert report.minimum_replay_rounds == 10
    assert report.minimum_chainlink_events == 3
    assert report.last_collected_block == 900
    assert report.bsc_chain_id == 56
    assert report.bsc_head_block == 1_000
    expected_manifest = build_shadow_runtime_campaign_manifest(
        MARKETS["BNBUSD"],
        oracle_proxy_anchor="0x" + "1" * 40,
        chainlink_aggregator_anchor="0x" + "2" * 40,
        config=_config(),
    )
    assert report.expected_campaign_manifest_digest == expected_manifest.digest
    assert report.expected_campaign_manifest == expected_manifest.canonical_payload()
    assert report.checks["campaign_manifest_constructible"] is True
    assert report.spot_lineage is not None
    assert report.spot_lineage.row_count == 100
    assert report.perp_lineage is not None
    assert report.perp_lineage.row_count == 100
    assert report.binance_spot_probe_rows == 1
    assert report.binance_perp_probe_rows == 1
    assert report.as_dict()["signing_enabled"] is False
    assert report.as_dict()["funded_execution"] is False


def test_preflight_missing_canonical_database_does_not_create_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    canonical = tmp_path / "missing.sqlite3"
    monkeypatch.setattr(
        shadow_preflight,
        "inspect_binance_trade_schema",
        lambda source: _schema(),
    )

    report = shadow_preflight.run_shadow_runtime_preflight(
        FakeRpc(),
        FakeClickHouse(),
        FakeBinance(),
        MARKETS["BNBUSD"],
        canonical,
        tmp_path / "shadow.sqlite3",
        config=_config(),
    )

    assert report.ready is False
    assert report.checks["canonical_database_exists"] is False
    assert report.checks["canonical_inputs_loadable"] is False
    assert report.checks["campaign_manifest_constructible"] is False
    assert report.expected_campaign_manifest_digest is None
    assert canonical.exists() is False


def test_preflight_fails_wrong_chain_stale_head_invalid_anchor_and_lineage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    canonical = tmp_path / "canonical.sqlite3"
    canonical.write_bytes(b"existing")
    metadata = _metadata()
    metadata["BNBUSD.oracle_anchor_address"] = "not-an-address"
    _patch_canonical(
        monkeypatch,
        inputs=_inputs(settled=3, rounds=8, chainlink=1),
        metadata=metadata,
    )
    monkeypatch.setattr(
        shadow_preflight,
        "inspect_binance_trade_schema",
        lambda source: _schema(),
    )

    report = shadow_preflight.run_shadow_runtime_preflight(
        FakeRpc(chain=1, head=800),
        FakeClickHouse(spot_rows=0, perp_rows=0),
        FakeBinance(),
        MARKETS["BNBUSD"],
        canonical,
        tmp_path / "shadow.sqlite3",
        config=_config(),
    )

    assert report.ready is False
    for name in (
        "chainlink_aggregator_anchor_valid",
        "campaign_manifest_constructible",
        "settled_history_capacity",
        "replay_capacity",
        "chainlink_history_capacity",
        "bsc_chain_id",
        "bsc_head_not_behind_canonical",
        "spot_lineage_present",
        "perp_lineage_present",
    ):
        assert report.checks[name] is False
        assert name in report.failures


def test_preflight_does_not_require_perp_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    canonical = tmp_path / "canonical.sqlite3"
    canonical.write_bytes(b"existing")
    _patch_canonical(monkeypatch)
    monkeypatch.setattr(
        shadow_preflight,
        "inspect_binance_trade_schema",
        lambda source: _schema(),
    )

    report = shadow_preflight.run_shadow_runtime_preflight(
        FakeRpc(),
        FakeClickHouse(perp_rows=0),
        FakeBinance(fail_venues=("um_futures",)),
        MARKETS["BNBUSD"],
        canonical,
        tmp_path / "shadow.sqlite3",
        config=_config(include_perp=False),
    )

    assert report.ready is True
    assert report.perp_lineage is None
    assert report.binance_perp_probe_rows is None
    assert report.checks["perp_lineage_present"] is True
    assert report.checks["binance_perp_reachable"] is True


def test_preflight_accepts_exact_bound_shadow_manifest_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    canonical = tmp_path / "canonical.sqlite3"
    canonical.write_bytes(b"existing")
    shadow = tmp_path / "shadow.sqlite3"
    config = _config()
    _patch_canonical(monkeypatch)
    monkeypatch.setattr(
        shadow_preflight,
        "inspect_binance_trade_schema",
        lambda source: _schema(),
    )

    store = ShadowLedgerStore(shadow)
    store.initialize()
    manifest = build_shadow_runtime_campaign_manifest(
        MARKETS["BNBUSD"],
        oracle_proxy_anchor="0x" + "1" * 40,
        chainlink_aggregator_anchor="0x" + "2" * 40,
        config=config,
    )
    store.bind_campaign_manifest(manifest)
    before_bytes = shadow.read_bytes()
    before_audit = store.audit(purge_rounds=config.inference.purge_rounds)

    report = shadow_preflight.run_shadow_runtime_preflight(
        FakeRpc(),
        FakeClickHouse(),
        FakeBinance(),
        MARKETS["BNBUSD"],
        canonical,
        shadow,
        config=config,
    )

    after_audit = store.audit(purge_rounds=config.inference.purge_rounds)
    assert report.ready is True
    assert report.checks["shadow_campaign_compatible"] is True
    assert report.shadow_ledger.binding_state == "bound"
    assert report.shadow_ledger.manifest_digest == manifest.digest
    assert report.expected_campaign_manifest_digest == manifest.digest
    assert shadow.read_bytes() == before_bytes
    assert after_audit.event_count == before_audit.event_count
    assert after_audit.campaign_manifest_digest == before_audit.campaign_manifest_digest


def test_preflight_rejects_conflicting_shadow_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    canonical = tmp_path / "canonical.sqlite3"
    canonical.write_bytes(b"existing")
    shadow = tmp_path / "shadow.sqlite3"
    config = _config()
    _patch_canonical(monkeypatch)
    monkeypatch.setattr(
        shadow_preflight,
        "inspect_binance_trade_schema",
        lambda source: _schema(),
    )

    changed = replace(
        config,
        inference=replace(
            config.inference,
            stake_wei=config.inference.stake_wei + 1,
        ),
    )
    store = ShadowLedgerStore(shadow)
    store.initialize()
    store.bind_campaign_manifest(
        build_shadow_runtime_campaign_manifest(
            MARKETS["BNBUSD"],
            oracle_proxy_anchor="0x" + "1" * 40,
            chainlink_aggregator_anchor="0x" + "2" * 40,
            config=changed,
        )
    )

    report = shadow_preflight.run_shadow_runtime_preflight(
        FakeRpc(),
        FakeClickHouse(),
        FakeBinance(),
        MARKETS["BNBUSD"],
        canonical,
        shadow,
        config=config,
    )

    assert report.ready is False
    assert report.checks["shadow_campaign_compatible"] is False
    assert "shadow_campaign_compatible" in report.failures
    assert report.shadow_ledger.binding_state == "bound"
    assert (
        report.shadow_ledger.manifest_digest
        != report.expected_campaign_manifest_digest
    )


def test_preflight_accepts_empty_unbound_shadow_ledger(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    canonical = tmp_path / "canonical.sqlite3"
    canonical.write_bytes(b"existing")
    shadow = tmp_path / "shadow.sqlite3"
    _patch_canonical(monkeypatch)
    monkeypatch.setattr(
        shadow_preflight,
        "inspect_binance_trade_schema",
        lambda source: _schema(),
    )
    ShadowLedgerStore(shadow).initialize()

    report = shadow_preflight.run_shadow_runtime_preflight(
        FakeRpc(),
        FakeClickHouse(),
        FakeBinance(),
        MARKETS["BNBUSD"],
        canonical,
        shadow,
        config=_config(),
    )

    assert report.ready is True
    assert report.checks["shadow_campaign_compatible"] is True
    assert report.shadow_ledger.binding_state == "empty_unbound"
    assert report.shadow_ledger.event_count == 0


def test_preflight_rejects_event_bearing_unbound_shadow_ledger(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    canonical = tmp_path / "canonical.sqlite3"
    canonical.write_bytes(b"existing")
    shadow = tmp_path / "shadow.sqlite3"
    _patch_canonical(monkeypatch)
    monkeypatch.setattr(
        shadow_preflight,
        "inspect_binance_trade_schema",
        lambda source: _schema(),
    )
    ShadowLedgerStore(shadow).initialize()
    with sqlite3.connect(shadow) as connection:
        connection.execute(
            """
            INSERT INTO shadow_ledger_events(
                kind, market, epoch, payload_json, previous_digest, event_digest
            ) VALUES ('prediction', 'BNBUSD', 1, '{}', ?, ?)
            """,
            ("0" * 64, "a" * 64),
        )
        connection.execute(
            """
            UPDATE shadow_ledger_state
            SET event_count = 1, head_digest = ?
            WHERE singleton = 1
            """,
            ("a" * 64,),
        )

    report = shadow_preflight.run_shadow_runtime_preflight(
        FakeRpc(),
        FakeClickHouse(),
        FakeBinance(),
        MARKETS["BNBUSD"],
        canonical,
        shadow,
        config=_config(),
    )

    assert report.ready is False
    assert report.checks["shadow_campaign_compatible"] is False
    assert report.shadow_ledger.binding_state == "event_bearing_unbound"
    assert report.shadow_ledger.event_count == 1


def test_preflight_rejects_malformed_shadow_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    canonical = tmp_path / "canonical.sqlite3"
    canonical.write_bytes(b"existing")
    shadow = tmp_path / "shadow.sqlite3"
    _patch_canonical(monkeypatch)
    monkeypatch.setattr(
        shadow_preflight,
        "inspect_binance_trade_schema",
        lambda source: _schema(),
    )
    ShadowLedgerStore(shadow).initialize()
    with sqlite3.connect(shadow) as connection:
        connection.execute(
            """
            INSERT INTO shadow_campaign_manifest(
                singleton, manifest_json, manifest_digest
            ) VALUES (1, '{}', ?)
            """,
            ("b" * 64,),
        )

    before = shadow.read_bytes()
    report = shadow_preflight.run_shadow_runtime_preflight(
        FakeRpc(),
        FakeClickHouse(),
        FakeBinance(),
        MARKETS["BNBUSD"],
        canonical,
        shadow,
        config=_config(),
    )

    assert report.ready is False
    assert report.checks["shadow_campaign_compatible"] is False
    assert report.shadow_ledger.binding_state == "invalid"
    assert report.shadow_ledger.errors == ("shadow_campaign_manifest_invalid",)
    assert shadow.read_bytes() == before


def test_preflight_external_failures_do_not_serialize_secrets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    canonical = tmp_path / "canonical.sqlite3"
    canonical.write_bytes(b"existing")
    _patch_canonical(monkeypatch)

    def failed_schema(source: object) -> ClickHouseBinanceSchemaReport:
        del source
        raise ClickHouseError("clickhouse-secret-password")

    monkeypatch.setattr(
        shadow_preflight,
        "inspect_binance_trade_schema",
        failed_schema,
    )

    report = shadow_preflight.run_shadow_runtime_preflight(
        FakeRpc(fail=True),
        FakeClickHouse(fail=True),
        FakeBinance(fail_venues=("spot", "um_futures")),
        MARKETS["BNBUSD"],
        canonical,
        tmp_path / "shadow.sqlite3",
        config=_config(),
    )
    rendered = json.dumps(report.as_dict(), sort_keys=True)

    assert report.ready is False
    assert "secret-rpc" not in rendered
    assert "secret-password" not in rendered
    assert "secret-token" not in rendered
    assert report.checks["bsc_chain_id"] is False
    assert report.checks["clickhouse_schema_ready"] is False
    assert report.checks["binance_spot_reachable"] is False
