from __future__ import annotations

import argparse
from collections.abc import Sequence

from pancake_prediction.abi import PREDICTION_EVENTS
from pancake_prediction.contracts import CHAIN_ID_BSC, MARKETS
from pancake_prediction.rpc import JsonRpcClient


def _quantity(value: object) -> int:
    text = str(value)
    return int(text, 16) if text.startswith("0x") else int(text)


def _start_round_topic() -> str:
    return next(spec.topic0 for spec in PREDICTION_EVENTS if spec.name == "StartRound")


def discover_fork_block(
    rpc: JsonRpcClient,
    *,
    market: str,
    lookback_blocks: int,
    confirmation_lag: int,
    chunk_size: int,
) -> tuple[int, int]:
    if market not in MARKETS:
        raise ValueError(f"unsupported market: {market}")
    if lookback_blocks < 1:
        raise ValueError("lookback_blocks must be positive")
    if confirmation_lag < 1:
        raise ValueError("confirmation_lag must be positive")
    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")
    if rpc.chain_id() != CHAIN_ID_BSC:
        raise RuntimeError("fork source RPC is not BSC mainnet chain id 56")

    safe_head = rpc.block_number() - confirmation_lag
    if safe_head <= 0:
        raise RuntimeError("fork source head is too low")
    lower_bound = max(0, safe_head - lookback_blocks)
    market_address = MARKETS[market].address
    topic = _start_round_topic()

    cursor = safe_head
    while cursor >= lower_bound:
        start = max(lower_bound, cursor - chunk_size + 1)
        logs = rpc.get_logs(
            market_address,
            start,
            cursor,
            topic0s=(topic,),
        )
        if logs:
            latest = max(
                logs,
                key=lambda log: (
                    _quantity(log["blockNumber"]),
                    _quantity(log.get("logIndex", 0)),
                ),
            )
            start_round_block = _quantity(latest["blockNumber"])
            fork_block = start_round_block + 1
            if fork_block > safe_head:
                raise RuntimeError("confirmed block after StartRound is not available yet")
            return fork_block, start_round_block
        if start == lower_bound:
            break
        cursor = start - 1

    raise RuntimeError(
        f"no StartRound event found in the last {lookback_blocks} confirmed blocks"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Discover a deterministic BSC fork block just after a Prediction StartRound."
    )
    parser.add_argument("--rpc-url", required=True)
    parser.add_argument("--market", choices=sorted(MARKETS), default="BNBUSD")
    parser.add_argument("--lookback-blocks", type=int, default=4_000)
    parser.add_argument("--confirmation-lag", type=int, default=4)
    parser.add_argument("--chunk-size", type=int, default=500)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    fork_block, _ = discover_fork_block(
        JsonRpcClient(str(args.rpc_url)),
        market=str(args.market),
        lookback_blocks=int(args.lookback_blocks),
        confirmation_lag=int(args.confirmation_lag),
        chunk_size=int(args.chunk_size),
    )
    print(fork_block)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
