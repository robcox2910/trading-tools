"""Revolut X candle data provider."""

from decimal import Decimal
from typing import Any

from trading_tools.clients.revolut_x.client import RevolutXClient
from trading_tools.core.models import Candle, Interval

_MAX_CANDLES_PER_REQUEST = 100

_INTERVAL_TO_MINUTES: dict[Interval, int] = {
    Interval.M5: 5,
    Interval.M15: 15,
    Interval.H1: 60,
    Interval.H4: 240,
    Interval.D1: 1440,
    Interval.W1: 10080,
}

_MS_PER_SECOND = 1000


class RevolutXCandleProvider:
    """Fetch candle data from the Revolut X API."""

    def __init__(self, client: RevolutXClient) -> None:
        """Initialize the Revolut X candle provider."""
        self._client = client

    async def get_candles(
        self,
        symbol: str,
        interval: Interval,
        start_ts: int,
        end_ts: int,
    ) -> list[Candle]:
        """Fetch candles from Revolut X API.

        Paginate in chunks of up to 100 candles per request.
        Timestamps are converted from seconds to milliseconds for the API.

        Raise ValueError if the interval is not supported by the API.
        """
        if interval not in _INTERVAL_TO_MINUTES:
            msg = f"Interval {interval.value} is not supported by the Revolut X API"
            raise ValueError(msg)

        minutes = _INTERVAL_TO_MINUTES[interval]
        path = f"/market-data/candles/{symbol}"
        since_ms = start_ts * _MS_PER_SECOND
        until_ms = end_ts * _MS_PER_SECOND

        all_candles: list[Candle] = []

        while since_ms < until_ms:
            params = {
                "interval": minutes,
                "since": since_ms,
                "until": until_ms,
            }
            response = await self._client.get(path, params=params)
            batch = [self._parse_candle(raw, symbol, interval) for raw in response["data"]]

            if not batch:
                break

            all_candles.extend(batch)

            if len(batch) < _MAX_CANDLES_PER_REQUEST:
                break

            # Advance past the last candle's timestamp
            since_ms = int(response["data"][-1]["start"]) + 1

        return all_candles

    @staticmethod
    def _parse_candle(raw: dict[str, Any], symbol: str, interval: Interval) -> Candle:
        """Parse a raw API candle dict into a Candle model."""
        return Candle(
            symbol=symbol,
            timestamp=int(raw["start"]) // _MS_PER_SECOND,
            open=Decimal(str(raw["open"])),
            high=Decimal(str(raw["high"])),
            low=Decimal(str(raw["low"])),
            close=Decimal(str(raw["close"])),
            volume=Decimal(str(raw["volume"])),
            interval=interval,
        )
