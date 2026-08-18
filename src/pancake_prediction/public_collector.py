from __future__ import annotations

from typing import Any

from .collector import HistoricalCollector
from .rpc import RpcError


class PublicHistoricalCollector(HistoricalCollector):
    """Historical collector hardened for restrictive unauthenticated RPCs.

    The base collector already halves block ranges when providers reject a broad
    ``eth_getLogs`` request. Some public BSC endpoints also enforce a result
    limit on a *single block* when several topic0 alternatives are queried at
    once. At that point a range split cannot make progress.

    This subclass keeps the base behavior for normal ranges, but once the query
    has reached one block it recursively partitions topic0 alternatives until
    each accepted request is small enough. Events are merged back in canonical
    EVM order and deduplicated defensively.
    """

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

        merged_blocks = dict(left_blocks)
        for number, block in right_blocks.items():
            existing = merged_blocks.get(number)
            if existing is not None and str(existing.get("hash", "")).lower() != str(
                block.get("hash", "")
            ).lower():
                raise RpcError(
                    f"canonical block mismatch across topic partitions at block {number}"
                )
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
