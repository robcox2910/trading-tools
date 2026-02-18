"""Revolut X candle data provider."""

from decimal import Decimal
from typing import Any

from trading_tools.clients.revolut_x.client import RevolutXClient
from trading_tools.core.models import Candle, Interval

_INTERVAL_TO_GRANULARITY: dict[Interval, str] = {
    Interval.M1: "60",
    Interval.M5: "300",
    Interval.M15: "900",
    Interval.H1: "3600",
    Interval.H4: "14400",
    Interval.D1: "86400",
    Interval.W1: "604800",
}


class RevolutXCandleProvider:
    """Fetches candle data from the Revolut X API."""

    def __init__(self, client: RevolutXClient) -> None:
        self._client = client

    async def get_candles(
        self,
        symbol: str,
        interval: Interval,
        start_ts: int,
        end_ts: int,
    ) -> list[Candle]:
        """Fetch candles from Revolut X API."""
        granularity = _INTERVAL_TO_GRANULARITY[interval]
        params = {
            "symbol": symbol,
            "granularity": granularity,
            "start": str(start_ts),
            "end": str(end_ts),
        }
        response = await self._client.get("/exchange/candles", params=params)
        return [self._parse_candle(raw, symbol, interval) for raw in response["data"]]

    @staticmethod
    def _parse_candle(raw: dict[str, Any], symbol: str, interval: Interval) -> Candle:
        return Candle(
            symbol=symbol,
            timestamp=int(raw["timestamp"]),
            open=Decimal(str(raw["open"])),
            high=Decimal(str(raw["high"])),
            low=Decimal(str(raw["low"])),
            close=Decimal(str(raw["close"])),
            volume=Decimal(str(raw["volume"])),
            interval=interval,
        )
