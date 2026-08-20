from __future__ import annotations

from typing import Any

from .abi import (
    CHAINLINK_EVENTS,
    PREDICTION_EVENTS,
    EventSpec,
    decode_address_result,
    function_selector,
)
from .collector import HistoricalCollector
from .contracts import Market
from .rpc import RpcError

_CHAINLINK_AGGREGATOR_CONFIRMED = EventSpec(
    "AggregatorConfirmed",
    "AggregatorConfirmed(address,address)",
    (("previous", "address"), ("latest", "address")),
    (),
)


class PublicHistoricalCollector(HistoricalCollector):
    """Historical collector hardened for restrictive unauthenticated RPCs."""

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
        effective_topic0s = (
            tuple(spec.topic0 for spec in specs) if topic0s is None else topic0s
        )
        return super()._collect_address_logs(
            chain_id=chain_id,
            address=address,
            market=market,
            source=source,
            specs=specs,
            from_block=from_block,
            to_block=to_block,
            topic0s=effective_topic0s,
        )

    def _fetch_consistent_chunk(
        self,
        *,
        address: str,
        start: int,
        end: int,
        topic0s: tuple[str, ...] | None,
    ) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]]]:
        try:
            return super()._fetch_consistent_chunk(
                address=address,
                start=start,
                end=end,
                topic0s=topic0s,
            )
        except RpcError as exc:
            if (
                start != end
                or topic0s is None
                or len(topic0s) <= 1
                or not self._is_log_range_error(exc)
            ):
                raise

        midpoint = len(topic0s) // 2
        left_topics = topic0s[:midpoint]
        right_topics = topic0s[midpoint:]
        left_logs, left_blocks = self._fetch_consistent_chunk(
            address=address,
            start=start,
            end=end,
            topic0s=left_topics,
        )
        right_logs, right_blocks = self._fetch_consistent_chunk(
            address=address,
            start=start,
            end=end,
            topic0s=right_topics,
        )
        return self._merge_log_results(
            left_logs,
            left_blocks,
            right_logs,
            right_blocks,
            mismatch_message="canonical block mismatch across topic partitions at block {block}",
        )

    @staticmethod
    def _merge_log_results(
        left_logs: list[dict[str, Any]],
        left_blocks: dict[int, dict[str, Any]],
        right_logs: list[dict[str, Any]],
        right_blocks: dict[int, dict[str, Any]],
        *,
        mismatch_message: str,
    ) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]]]:
        merged_blocks = dict(left_blocks)
        for number, block in right_blocks.items():
            existing = merged_blocks.get(number)
            if existing is not None and str(existing.get("hash", "")).lower() != str(
                block.get("hash", "")
            ).lower():
                raise RpcError(mismatch_message.format(block=number))
            merged_blocks[number] = block

        unique_logs: dict[tuple[str, str, str], dict[str, Any]] = {}
        for log in (*left_logs, *right_logs):
            key = (
                str(log.get("blockHash", "")).lower(),
                str(log.get("transactionHash", "")).lower(),
                str(log.get("logIndex", "")).lower(),
            )
            unique_logs[key] = log
        logs = sorted(
            unique_logs.values(),
            key=lambda item: (
                int(str(item["blockNumber"]), 16),
                int(str(item.get("transactionIndex", "0x0")), 16),
                int(str(item["logIndex"]), 16),
            ),
        )
        return logs, merged_blocks

    def _fetch_consistent_range(
        self,
        *,
        address: str,
        start: int,
        end: int,
        topic0s: tuple[str, ...],
    ) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]]]:
        """Read a log range without checkpoints, splitting only on provider range limits."""

        try:
            return self._fetch_consistent_chunk(
                address=address,
                start=start,
                end=end,
                topic0s=topic0s,
            )
        except RpcError as exc:
            if start >= end or not self._is_log_range_error(exc):
                raise
        midpoint = (start + end) // 2
        left_logs, left_blocks = self._fetch_consistent_range(
            address=address,
            start=start,
            end=midpoint,
            topic0s=topic0s,
        )
        right_logs, right_blocks = self._fetch_consistent_range(
            address=address,
            start=midpoint + 1,
            end=end,
            topic0s=topic0s,
        )
        return self._merge_log_results(
            left_logs,
            left_blocks,
            right_logs,
            right_blocks,
            mismatch_message="canonical block mismatch across range partitions at block {block}",
        )

    def _chainlink_aggregator_at(
        self,
        oracle_proxy: str,
        block: int | str = "latest",
    ) -> str:
        result = self.rpc.eth_call(
            oracle_proxy,
            function_selector("aggregator()"),
            block,
        )
        aggregator = decode_address_result(result).lower()
        if aggregator == "0x" + "00" * 20:
            raise RpcError("Chainlink proxy returned the zero aggregator address")
        return aggregator

    def prove_latest_oracle_stable_since(
        self,
        market: Market,
        *,
        from_block: int,
        through_block: int,
    ) -> dict[str, object]:
        """Prove both the Prediction oracle proxy and its Chainlink aggregator are stable.

        The proof is intentionally stateless. It reads the latest Prediction
        oracle proxy and that proxy's current ``aggregator()`` first, captures a
        post-read head, then rejects any Prediction ``NewOracle`` or Chainlink
        proxy ``AggregatorConfirmed`` event from the requested window start
        through that head.
        """

        if from_block < 0 or through_block < from_block:
            raise ValueError("invalid oracle stability proof range")
        self.validate_chain()
        oracle_proxy = self.oracle_at(market, "latest").lower()
        chainlink_aggregator = self._chainlink_aggregator_at(oracle_proxy, "latest")
        observed_head = self.rpc.block_number()
        proof_through_block = max(through_block, observed_head)

        new_oracle_spec = next(
            (spec for spec in PREDICTION_EVENTS if spec.name == "NewOracle"),
            None,
        )
        if new_oracle_spec is None:
            raise RuntimeError("NewOracle event specification is unavailable")
        prediction_changes, _ = self._fetch_consistent_range(
            address=market.address,
            start=from_block,
            end=proof_through_block,
            topic0s=(new_oracle_spec.topic0,),
        )
        if prediction_changes:
            raise RpcError(
                "latest oracle cannot prove the window start: NewOracle was observed "
                f"between blocks {from_block} and {proof_through_block}"
            )

        aggregator_changes, _ = self._fetch_consistent_range(
            address=oracle_proxy,
            start=from_block,
            end=proof_through_block,
            topic0s=(_CHAINLINK_AGGREGATOR_CONFIRMED.topic0,),
        )
        if aggregator_changes:
            raise RpcError(
                "latest Chainlink aggregator cannot prove the window start: "
                "AggregatorConfirmed was observed between blocks "
                f"{from_block} and {proof_through_block}"
            )

        self.store.record_metadata(
            f"{market.symbol}.recent_oracle_stability_proof",
            f"{from_block}:{proof_through_block}:{oracle_proxy}:{chainlink_aggregator}",
        )
        return {
            "oracle": oracle_proxy,
            "chainlink_aggregator": chainlink_aggregator,
            "from_block": from_block,
            "through_block": proof_through_block,
            "new_oracle_events": 0,
            "aggregator_confirmed_events": 0,
            "method": (
                "latest_prediction_oracle_and_chainlink_aggregator_then_"
                "stateless_change_scan_through_post_read_head"
            ),
        }

    def collect_chainlink_feed(
        self,
        market: Market,
        *,
        aggregator_address: str,
        from_block: int,
        to_block: int,
    ) -> dict[str, object]:
        """Collect AnswerUpdated from a separately proven Chainlink aggregator address."""

        if from_block < 0 or to_block < from_block:
            raise ValueError("invalid Chainlink collection range")
        chain_id = self.validate_chain()
        run_id = self.store.begin_collector_run(
            chain_id=chain_id,
            market=market.symbol,
            contract_address=aggregator_address.lower(),
            from_block=from_block,
            to_block=to_block,
            details={
                "source": "chainlink",
                "chunk_size": self.chunk_size,
                "reorg_lookback": self.reorg_lookback,
                "consistency_retries": self.consistency_retries,
            },
        )
        try:
            count, _ = self._collect_address_logs(
                chain_id=chain_id,
                address=aggregator_address,
                market=market.symbol,
                source="chainlink",
                specs=CHAINLINK_EVENTS,
                from_block=from_block,
                to_block=to_block,
            )
        except Exception as exc:
            self.store.finish_collector_run(
                run_id,
                status="failed",
                details={"error_type": type(exc).__name__, "error": str(exc)[:500]},
            )
            raise
        report: dict[str, object] = {
            "market": market.symbol,
            "from_block": from_block,
            "to_block": to_block,
            "aggregator_address": aggregator_address.lower(),
            "chainlink_events_inserted": count,
            "collector_run_id": run_id,
        }
        self.store.finish_collector_run(run_id, status="success", details=report)
        return report
