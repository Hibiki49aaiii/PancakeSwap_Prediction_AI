from __future__ import annotations

from typing import Any

from .abi import PREDICTION_EVENTS, EventSpec
from .collector import HistoricalCollector
from .contracts import Market
from .rpc import RpcError


class PublicHistoricalCollector(HistoricalCollector):
    """Historical collector hardened for restrictive unauthenticated RPCs.

    The base collector already halves block ranges when providers reject a broad
    ``eth_getLogs`` request. Some public BSC endpoints also enforce a result
    limit on a *single block* when several topic0 alternatives are queried at
    once. At that point a range split cannot make progress.

    Public endpoints can also reject an unfiltered address-only log request.
    For this subclass, an omitted topic filter therefore means "all known event
    specs" rather than a raw address-only query. The resulting explicit topic0
    alternatives can then be partitioned without dropping any known event type.
    """

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

    def prove_latest_oracle_stable_since(
        self,
        market: Market,
        *,
        from_block: int,
        through_block: int,
    ) -> dict[str, object]:
        """Fail closed unless the latest oracle is proven stable since ``from_block``.

        A public full node may not support historical ``oracle()`` state reads.
        For a recent window, the latest oracle is still valid for the complete
        window if no ``NewOracle`` event occurred from the window start through
        the observed head. Any observed change makes the pre-change oracle
        ambiguous without additional historical evidence, so this method
        rejects rather than guessing.
        """

        if from_block < 0 or through_block < from_block:
            raise ValueError("invalid oracle stability proof range")
        chain_id = self.validate_chain()
        new_oracle_specs = tuple(
            spec for spec in PREDICTION_EVENTS if spec.name == "NewOracle"
        )
        if len(new_oracle_specs) != 1:
            raise RuntimeError("expected exactly one NewOracle event specification")
        spec = new_oracle_specs[0]
        event_count, observed_oracles = self._collect_address_logs(
            chain_id=chain_id,
            address=market.address,
            market=market.symbol,
            source="oracle_proof",
            specs=new_oracle_specs,
            from_block=from_block,
            to_block=through_block,
            topic0s=(spec.topic0,),
        )
        if event_count != 0 or observed_oracles:
            raise RpcError(
                "latest oracle cannot prove the window start: NewOracle was observed "
                f"between blocks {from_block} and {through_block}"
            )
        oracle = self.oracle_at(market, "latest").lower()
        self.store.record_metadata(
            f"{market.symbol}.recent_oracle_stability_proof",
            f"{from_block}:{through_block}:{oracle}",
        )
        return {
            "oracle": oracle,
            "from_block": from_block,
            "through_block": through_block,
            "new_oracle_events": event_count,
            "method": "latest_oracle_plus_no_NewOracle_since_window_start",
        }
