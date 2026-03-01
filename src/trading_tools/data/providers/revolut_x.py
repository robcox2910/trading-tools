"""Revolut X candle data provider.

Fetch OHLCV candle data from the Revolut X exchange API. The API
returns at most 100 candles per request, so this provider paginates
automatically by advancing the ``since`` parameter past the last
returned candle until the full requested range is covered.
"""

from decimal import Decimal
from typing import Any, cast

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
    """Fetch candle data from the Revolut X exchange API.

    Implement the ``CandleProvider`` protocol. The Revolut X API uses
    millisecond timestamps and returns at most 100 candles per request,
    so this provider converts between seconds and milliseconds and
    paginates transparently.
    """

    def __init__(self, client: RevolutXClient) -> None:
        """Initialize the provider with an authenticated Revolut X client.

        Args:
            client: An already-configured ``RevolutXClient`` instance.

        """
        self._client = client

    async def get_candles(
        self,
        symbol: str,
        interval: Interval,
        start_ts: int,
        end_ts: int,
    ) -> list[Candle]:
        """Fetch candles from the Revolut X API for the given range.

        Paginate in chunks of up to 100 candles per request, advancing
        the ``since`` parameter past the last candle each time.
        Timestamps are converted from seconds to milliseconds for the API.

        Args:
            symbol: Trading pair (e.g. ``BTC-USD``).
            interval: Candle time interval (must be in ``_INTERVAL_TO_MINUTES``).
            start_ts: Start Unix timestamp in seconds.
            end_ts: End Unix timestamp in seconds.

        Returns:
            List of ``Candle`` objects sorted by timestamp.

        Raises:
            ValueError: If the interval is not supported by the API.

        """
        if interval not in _INTERVAL_TO_MINUTES:
            msg = f"Interval {interval.value} is not supported by the Revolut X API"
            raise ValueError(msg)

        minutes = _INTERVAL_TO_MINUTES[interval]
        path = f"/candles/{symbol}"
        since_ms = start_ts * _MS_PER_SECOND
        until_ms = end_ts * _MS_PER_SECOND

        all_candles: list[Candle] = []
        max_iterations = 10_000

        for _ in range(max_iterations):
            if since_ms >= until_ms:
                break

            params = {
                "interval": minutes,
                "since": since_ms,
                "until": until_ms,
            }
            response = await self._client.get(path, params=params)
            data = response.get("data")
            if not isinstance(data, list):
                break

            rows = cast("list[dict[str, Any]]", data)
            batch = [self._parse_candle(raw, symbol, interval) for raw in rows]

            if not batch:
                break

            all_candles.extend(batch)

            if len(batch) < _MAX_CANDLES_PER_REQUEST:
                break

            # Advance past the last candle's timestamp
            next_since = int(rows[-1]["start"]) + 1
            if next_since <= since_ms:
                break
            since_ms = next_since

        return all_candles

    @staticmethod
    def _parse_candle(raw: dict[str, Any], symbol: str, interval: Interval) -> Candle:
        """Parse a raw API response dict into a ``Candle`` model.

        Convert the millisecond ``start`` timestamp to seconds and wrap
        all numeric fields in ``Decimal`` for lossless arithmetic.
        """
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
