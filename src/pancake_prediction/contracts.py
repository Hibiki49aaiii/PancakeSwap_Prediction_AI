from __future__ import annotations

from dataclasses import dataclass

CHAIN_ID_BSC = 56
DEFAULT_ROUND_INTERVAL_SECONDS = 300
DOCUMENTED_TREASURY_FEE_BPS = 300


@dataclass(frozen=True, slots=True)
class PredictionMarket:
    symbol: str
    address: str
    settlement_asset: str
    stake_asset: str = "BNB"


MARKETS: dict[str, PredictionMarket] = {
    "BNBUSD": PredictionMarket(
        symbol="BNBUSD",
        address="0x18b2a687610328590bc8f2e5fedde3b582a49cda",
        settlement_asset="BNB/USD",
    ),
    "BTCUSD": PredictionMarket(
        symbol="BTCUSD",
        address="0x48781a7d35f6137a9135bbb984af65fd6ab25618",
        settlement_asset="BTC/USD",
    ),
    "ETHUSD": PredictionMarket(
        symbol="ETHUSD",
        address="0x7451f994a8d510cbcb46cf57d50f31f188ff58f5",
        settlement_asset="ETH/USD",
    ),
}


def market(symbol: str) -> PredictionMarket:
    try:
        return MARKETS[symbol.upper()]
    except KeyError as exc:
        raise ValueError(f"unsupported Prediction market: {symbol}") from exc
