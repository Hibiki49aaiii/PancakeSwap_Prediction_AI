from __future__ import annotations

from typing import Any

from .abi import PREDICTION_EVENTS, EventSpec
from .collector import HistoricalCollector
from .contracts import Market
from .rpc import RpcError


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
            mismatch_message=f"canonical block mismatch across topic partitions at block {{block}}",
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
            mismatch_message=f"canonical block mismatch across range partitions at block {{block}}",
        )

    def prove_latest_oracle_stable_since(
        self,
        market: Market,
        *,
        from_block: int,
        through_block: int,
    ) -> dict[str, object]:
        """Fail closed unless one latest-oracle snapshot is stable since the window start.

        Read ``oracle()`` first, then capture a head block and scan ``NewOracle``
        through that head. The proof scan intentionally bypasses collector
        checkpoints so a repeated failed proof cannot later pass just because a
        previous attempt advanced a resume cursor.
        """

        if from_block < 0 or through_block < from_block:
            raise ValueError("invalid oracle stability proof range")
        self.validate_chain()
        oracle = self.oracle_at(market, "latest").lower()
        observed_head = self.rpc.block_number()
        proof_through_block = max(through_block, observed_head)

        new_oracle_specs = tuple(
            spec for spec in PREDICTION_EVENTS if spec.name == "NewOracle"
        )
        if len(new_oracle_specs) != 1:
            raise RuntimeError("expected exactly one NewOracle event specification")
        spec = new_oracle_specs[0]
        logs, _ = self._fetch_consistent_range(
            address=market.address,
            start=from_block,
            end=proof_through_block,
            topic0s=(spec.topic0,),
        )
        if logs:
            raise RpcError(
                "latest oracle cannot prove the window start: NewOracle was observed "
                f"between blocks {from_block} and {proof_through_block}"
            )
        self.store.record_metadata(
            f"{market.symbol}.recent_oracle_stability_proof",
            f"{from_block}:{proof_through_block}:{oracle}",
        )
        return {
            "oracle": oracle,
            "from_block": from_block,
            "through_block": proof_through_block,
            "new_oracle_events": 0,
            "method": "latest_oracle_then_stateless_no_NewOracle_through_post_read_head",
        }
