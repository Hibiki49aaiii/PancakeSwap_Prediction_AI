from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Market:
    symbol: str
    address: str
    deployment_block_hint: int | None = None
    creation_tx_hash: str | None = None


CHAIN_ID_BSC = 56

# PancakeSwap official Prediction documentation, verified 2026-08-16.
MARKETS: dict[str, Market] = {
    "BNBUSD": Market(
        "BNBUSD",
        "0x18B2A687610328590Bc8F2e5fEdDe3b582A49cdA",
        10_333_825,
        "0x9c223f125a698edadd81665082f4de89a20d44ad267e83cf2210a28225a5c89a",
    ),
    "BTCUSD": Market("BTCUSD", "0x48781a7d35f6137a9135Bbb984AF65fd6AB25618"),
    "ETHUSD": Market(
        "ETHUSD",
        "0x7451F994A8D510CBCB46cF57D50F31F188Ff58F5",
        60_087_359,
    ),
}
