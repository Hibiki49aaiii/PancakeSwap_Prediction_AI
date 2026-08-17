from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Protocol

from .contracts import Market
from .rpc import RpcError
from .rpc_probe import ArchiveProbeResult, HistoricalStateRpc, probe_archive_state


class HistoricalPreflightRpc(HistoricalStateRpc, Protocol):
    def block_number(self) -> int: ...


@dataclass(frozen=True, slots=True)
class HistoricalPreflightResult:
    market: str
    head_block: int
    deployment_block: int
    archive_probe: ArchiveProbeResult

    def as_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["archive_probe"] = self.archive_probe.as_dict()
        return payload


def _code_present(code: str) -> bool:
    return code.lower() not in {"", "0x", "0x0"}


def discover_deployment_block(
    rpc: HistoricalPreflightRpc,
    address: str,
    *,
    upper_block: int,
) -> int:
    if upper_block < 0:
        raise ValueError("upper_block must be non-negative")
    if not _code_present(rpc.get_code(address, upper_block)):
        raise RpcError(f"no contract code at {address} by block {upper_block}")

    low = 0
    high = upper_block
    while low < high:
        mid = (low + high) // 2
        if _code_present(rpc.get_code(address, mid)):
            high = mid
        else:
            low = mid + 1
    return low


def run_historical_preflight(
    rpc: HistoricalPreflightRpc,
    market: Market,
) -> HistoricalPreflightResult:
    head_block = rpc.block_number()
    if head_block < 0:
        raise RpcError("RPC returned a negative head block")

    deployment_block = discover_deployment_block(
        rpc,
        market.address,
        upper_block=head_block,
    )
    archive_probe = probe_archive_state(rpc, market, deployment_block)
    return HistoricalPreflightResult(
        market=market.symbol,
        head_block=head_block,
        deployment_block=deployment_block,
        archive_probe=archive_probe,
    )
