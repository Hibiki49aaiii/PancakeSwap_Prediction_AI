from __future__ import annotations

import hashlib
from contextlib import suppress
from dataclasses import dataclass
from itertools import pairwise
from typing import Any, Protocol

from .abi import (
    CHAINLINK_EVENTS,
    PREDICTION_EVENTS,
    EventSpec,
    decode_address_result,
    decode_event,
    function_selector,
)
from .contracts import CHAIN_ID_BSC, Market
from .rpc import RpcError
from .store import EventStore


class ReadOnlyRpc(Protocol):
    def chain_id(self) -> int: ...
    def block_number(self) -> int: ...
    def block(self, number: int) -> dict[str, Any]: ...
    def get_logs(
        self,
        address: str,
        from_block: int,
        to_block: int,
        *,
        topic0s: tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]: ...
    def get_code(self, address: str, block: int | str = "latest") -> str: ...
    def eth_call(self, to: str, data: str, block: int | str = "latest") -> str: ...


ORACLE_SELECTOR = function_selector("oracle()")
ANALYTIC_PREDICTION_EVENT_NAMES = {
    "StartRound",
    "BetBull",
    "BetBear",
    "LockRound",
    "EndRound",
    "RewardsCalculated",
    "NewTreasuryFee",
    "NewBufferAndIntervalSeconds",
}


@dataclass(slots=True)
class HistoricalCollector:
    rpc: ReadOnlyRpc
    store: EventStore
    chunk_size: int = 2_000
    reorg_lookback: int = 64
    consistency_retries: int = 3

    def validate_chain(self) -> int:
        chain_id = self.rpc.chain_id()
        if chain_id != CHAIN_ID_BSC:
            raise RpcError(f"expected BSC chain_id={CHAIN_ID_BSC}, got {chain_id}")
        return chain_id

    def discover_deployment_block(self, address: str, upper_block: int | None = None) -> int:
        high = self.rpc.block_number() if upper_block is None else upper_block
        if self.rpc.get_code(address, high) in ("0x", "0x0"):
            raise RpcError(f"no contract code at {address} by block {high}")
        low = 0
        while low < high:
            mid = (low + high) // 2
            if self.rpc.get_code(address, mid) not in ("0x", "0x0"):
                high = mid
            else:
                low = mid + 1
        return low

    def oracle_at(self, market: Market, block: int | str = "latest") -> str:
        return decode_address_result(self.rpc.eth_call(market.address, ORACLE_SELECTOR, block))

    def check_recent_reorgs(self, chain_id: int, latest: int) -> list[int]:
        changed: list[int] = []
        lower = max(0, latest - self.reorg_lookback)
        for row in self.store.canonical_blocks_from(chain_id, lower):
            number = int(row["number"])
            block = self.rpc.block(number)
            if str(block["hash"]).lower() != str(row["hash"]).lower():
                changed.append(number)
                self.store.upsert_block(chain_id, block)
        return changed

    @staticmethod
    def _is_log_range_error(exc: RpcError) -> bool:
        message = str(exc).lower()
        markers = (
            "block range",
            "max range",
            "maximum block",
            "query returned more than",
            "too many results",
            "response size",
            "result set",
            "log response size",
            "-32005",
        )
        return any(marker in message for marker in markers)

    @staticmethod
    def _checkpoint_key(
        *,
        chain_id: int,
        address: str,
        market: str | None,
        source: str,
        specs: tuple[EventSpec, ...],
        from_block: int,
        topic0s: tuple[str, ...] | None,
    ) -> str:
        identity = {
            "chain_id": chain_id,
            "address": address.lower(),
            "market": market,
            "source": source,
            "specs": tuple((spec.name, spec.topic0) for spec in specs),
            "from_block": from_block,
            "topic0s": None if topic0s is None else tuple(sorted(topic0s)),
        }
        digest = hashlib.sha256(repr(identity).encode()).hexdigest()[:24]
        return f"collector.progress.{digest}"

    def _resume_cursor(self, checkpoint_key: str, from_block: int, to_block: int) -> int:
        checkpoint_text = self.store.metadata(checkpoint_key)
        if checkpoint_text is None:
            return from_block
        try:
            checkpoint = int(checkpoint_text)
        except ValueError:
            return from_block
        if checkpoint < from_block:
            return from_block
        completed_through = min(checkpoint, to_block)
        return max(from_block, completed_through + 1 - self.reorg_lookback)

    def _fetch_consistent_chunk(
        self,
        *,
        address: str,
        start: int,
        end: int,
        topic0s: tuple[str, ...] | None,
    ) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]]]:
        if self.consistency_retries < 1:
            raise ValueError("consistency_retries must be positive")
        for _attempt in range(self.consistency_retries):
            logs = self.rpc.get_logs(address, start, end, topic0s=topic0s)
            blocks: dict[int, dict[str, Any]] = {}
            consistent = True
            for log in logs:
                block_number = int(str(log["blockNumber"]), 16)
                block = blocks.setdefault(block_number, self.rpc.block(block_number))
                if str(log.get("blockHash", "")).lower() != str(block["hash"]).lower():
                    consistent = False
                    break
            if consistent:
                ordered = sorted(blocks)
                for left, right in pairwise(ordered):
                    if right != left + 1:
                        continue
                    parent_hash = str(blocks[right]["parentHash"]).lower()
                    previous_hash = str(blocks[left]["hash"]).lower()
                    if parent_hash != previous_hash:
                        consistent = False
                        break
            if consistent:
                return logs, blocks
        raise RpcError(f"canonical block/log mismatch persisted for chunk {start}-{end}")

    def _collect_address_logs(
        self,
        *,
        chain_id: int,
        address: str,
        market: str | None,
        source: str,
        specs: tuple[EventSpec, ...],
        from_block: int,
        to_block: int,
        topic0s: tuple[str, ...] | None = None,
    ) -> tuple[int, set[str]]:
        if self.chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        if self.reorg_lookback < 0:
            raise ValueError("reorg_lookback must be non-negative")
        checkpoint_key = self._checkpoint_key(
            chain_id=chain_id,
            address=address,
            market=market,
            source=source,
            specs=specs,
            from_block=from_block,
            topic0s=topic0s,
        )
        previous_checkpoint_text = self.store.metadata(checkpoint_key)
        previous_checkpoint = from_block - 1
        if previous_checkpoint_text is not None:
            with suppress(ValueError):
                previous_checkpoint = max(previous_checkpoint, int(previous_checkpoint_text))

        inserted = 0
        new_oracles: set[str] = set()
        cursor = self._resume_cursor(checkpoint_key, from_block, to_block)
        effective_chunk_size = self.chunk_size
        while cursor <= to_block:
            end = min(cursor + effective_chunk_size - 1, to_block)
            try:
                logs, blocks = self._fetch_consistent_chunk(
                    address=address,
                    start=cursor,
                    end=end,
                    topic0s=topic0s,
                )
            except RpcError as exc:
                if effective_chunk_size <= 1 or not self._is_log_range_error(exc):
                    raise
                effective_chunk_size = max(1, effective_chunk_size // 2)
                continue
            for block_number in sorted(blocks):
                self.store.upsert_block(chain_id, blocks[block_number])
            for log in logs:
                decoded_event = decode_event(log, specs)
                event_name: str | None = None
                decoded: dict[str, object] | None = None
                if decoded_event is not None:
                    event_name, decoded = decoded_event
                    if event_name == "NewOracle" and isinstance(decoded.get("oracle"), str):
                        new_oracles.add(str(decoded["oracle"]))
                if self.store.insert_event(
                    chain_id=chain_id,
                    contract_address=address,
                    market=market,
                    source=source,
                    log=log,
                    event_name=event_name,
                    decoded=decoded,
                ):
                    inserted += 1
            previous_checkpoint = max(previous_checkpoint, end)
            self.store.record_monotonic_int_metadata(
                checkpoint_key,
                previous_checkpoint,
            )
            cursor = end + 1
        return inserted, new_oracles

    def _stored_oracle_addresses(self, market: Market) -> set[str]:
        addresses: set[str] = set()
        for decoded in self.store.canonical_decoded_events(
            market=market.symbol,
            source="prediction",
            event_name="NewOracle",
        ):
            oracle = decoded.get("oracle")
            if isinstance(oracle, str):
                addresses.add(oracle.lower())
        return addresses

    def collect_market(
        self,
        market: Market,
        from_block: int,
        to_block: int,
        *,
        include_chainlink: bool = True,
        prediction_analytic_only: bool = False,
    ) -> dict[str, object]:
        if from_block < 0 or to_block < from_block:
            raise ValueError("invalid collection range")
        chain_id = self.validate_chain()
        run_id = self.store.begin_collector_run(
            chain_id=chain_id,
            market=market.symbol,
            contract_address=market.address,
            from_block=from_block,
            to_block=to_block,
            details={
                "chunk_size": self.chunk_size,
                "reorg_lookback": self.reorg_lookback,
                "consistency_retries": self.consistency_retries,
            },
        )
        try:
            report = self._collect_market_inner(
                chain_id=chain_id,
                market=market,
                from_block=from_block,
                to_block=to_block,
                include_chainlink=include_chainlink,
                prediction_analytic_only=prediction_analytic_only,
            )
        except Exception as exc:
            self.store.finish_collector_run(
                run_id,
                status="failed",
                details={"error_type": type(exc).__name__, "error": str(exc)[:500]},
            )
            raise
        report["collector_run_id"] = run_id
        self.store.finish_collector_run(run_id, status="success", details=report)
        return report

    def _collect_market_inner(
        self,
        *,
        chain_id: int,
        market: Market,
        from_block: int,
        to_block: int,
        include_chainlink: bool,
        prediction_analytic_only: bool,
    ) -> dict[str, object]:
        reorgs = self.check_recent_reorgs(chain_id, to_block)
        prediction_topic0s = None
        if prediction_analytic_only:
            requested_names = set(ANALYTIC_PREDICTION_EVENT_NAMES)
            if include_chainlink:
                requested_names.add("NewOracle")
            prediction_topic0s = tuple(
                spec.topic0 for spec in PREDICTION_EVENTS if spec.name in requested_names
            )
        prediction_count, new_oracles = self._collect_address_logs(
            chain_id=chain_id,
            address=market.address,
            market=market.symbol,
            source="prediction",
            specs=PREDICTION_EVENTS,
            from_block=from_block,
            to_block=to_block,
            topic0s=prediction_topic0s,
        )

        oracle_addresses: set[str] = set()
        oracle_count = 0
        if include_chainlink:
            oracle_addresses = self._stored_oracle_addresses(market)
            oracle_addresses.update(address.lower() for address in new_oracles)
            try:
                oracle_addresses.add(self.oracle_at(market, to_block).lower())
            except RpcError:
                oracle_addresses.add(self.oracle_at(market, "latest").lower())
            try:
                oracle_addresses.add(self.oracle_at(market, from_block).lower())
            except RpcError:
                self.store.record_metadata(
                    f"{market.symbol}.historical_oracle_state", "unavailable"
                )
            for oracle in sorted(oracle_addresses):
                count, _ = self._collect_address_logs(
                    chain_id=chain_id,
                    address=oracle,
                    market=market.symbol,
                    source="chainlink",
                    specs=CHAINLINK_EVENTS,
                    from_block=from_block,
                    to_block=to_block,
                )
                oracle_count += count

        self.store.record_monotonic_int_metadata(
            f"{market.symbol}.last_collected_block",
            to_block,
        )
        return {
            "market": market.symbol,
            "from_block": from_block,
            "to_block": to_block,
            "prediction_events_inserted": prediction_count,
            "chainlink_events_inserted": oracle_count,
            "oracle_addresses": sorted(oracle_addresses),
            "include_chainlink": include_chainlink,
            "prediction_analytic_only": prediction_analytic_only,
            "reorg_blocks_detected": reorgs,
        }
