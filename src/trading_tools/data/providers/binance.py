"""Binance candle data provider.

Fetch OHLCV candle data from the public Binance klines REST API. The
API returns at most 1 000 candles per request, so this provider
paginates automatically by advancing the ``startTime`` parameter past
the last returned candle until the full requested range is covered.
"""

from decimal import Decimal
from typing import Any

from trading_tools.clients.binance.client import BinanceClient
from trading_tools.core.models import Candle, Interval

_MAX_CANDLES_PER_REQUEST = 1000

_MS_PER_SECOND = 1000

_INTERVAL_TO_BINANCE: dict[Interval, str] = {
    Interval.M1: "1m",
    Interval.M5: "5m",
    Interval.M15: "15m",
    Interval.H1: "1h",
    Interval.H4: "4h",
    Interval.D1: "1d",
    Interval.W1: "1w",
}

# Kline array indices
_IDX_OPEN_TIME = 0
_IDX_OPEN = 1
_IDX_HIGH = 2
_IDX_LOW = 3
_IDX_CLOSE = 4
_IDX_VOLUME = 5


def _symbol_to_binance(symbol: str) -> str:
    """Convert a user-facing symbol to Binance format.

    ``BTC-USD`` becomes ``BTCUSDT``: strip the hyphen and replace a
    trailing ``USD`` with ``USDT``.
    """
    raw = symbol.replace("-", "")
    if raw.endswith("USD") and not raw.endswith("USDT"):
        raw = f"{raw}T"
    return raw


class BinanceCandleProvider:
    """Fetch candle data from the Binance public klines REST API.

    Implement the ``CandleProvider`` protocol. Symbols are converted
    from the internal ``BTC-USD`` format to Binance's ``BTCUSDT``
    format automatically. The API uses millisecond timestamps and
    returns kline arrays, which are parsed into ``Candle`` objects.
    """

    def __init__(self, client: BinanceClient) -> None:
        """Initialize the provider with a Binance HTTP client.

        Args:
            client: A ``BinanceClient`` instance for making API requests.

        """
        self._client = client

    async def get_candles(
        self,
        symbol: str,
        interval: Interval,
        start_ts: int,
        end_ts: int,
    ) -> list[Candle]:
        """Fetch candles from the Binance klines endpoint for the given range.

        Paginate in chunks of up to 1 000 candles per request, advancing
        the ``startTime`` parameter past the last candle each time.
        Timestamps are converted from seconds to milliseconds for the API.

        Args:
            symbol: Trading pair (e.g. ``BTC-USD``), auto-converted to ``BTCUSDT``.
            interval: Candle time interval (must be in ``_INTERVAL_TO_BINANCE``).
            start_ts: Start Unix timestamp in seconds.
            end_ts: End Unix timestamp in seconds.

        Returns:
            List of ``Candle`` objects sorted by timestamp.

        Raises:
            ValueError: If the interval is not supported by the Binance API.

        """
        if interval not in _INTERVAL_TO_BINANCE:
            msg = f"Interval {interval.value} is not supported by the Binance API"
            raise ValueError(msg)

        binance_symbol = _symbol_to_binance(symbol)
        binance_interval = _INTERVAL_TO_BINANCE[interval]
        start_ms = start_ts * _MS_PER_SECOND
        end_ms = end_ts * _MS_PER_SECOND

        all_candles: list[Candle] = []
        max_iterations = 10_000

        for _ in range(max_iterations):
            if start_ms >= end_ms:
                break

            params: dict[str, Any] = {
                "symbol": binance_symbol,
                "interval": binance_interval,
                "startTime": start_ms,
                "endTime": end_ms,
                "limit": _MAX_CANDLES_PER_REQUEST,
            }
            raw_list: list[list[Any]] = await self._client.get("/api/v3/klines", params=params)
            batch = [self._parse_candle(raw, symbol, interval) for raw in raw_list]

            if not batch:
                break

            all_candles.extend(batch)

            if len(batch) < _MAX_CANDLES_PER_REQUEST:
                break

            # Advance past the last candle's open time
            next_start = int(raw_list[-1][_IDX_OPEN_TIME]) + 1
            if next_start <= start_ms:
                break
            start_ms = next_start

        return all_candles

    @staticmethod
    def _parse_candle(raw: list[Any], symbol: str, interval: Interval) -> Candle:
        """Parse a raw Binance kline array into a ``Candle`` model.

        Convert the millisecond open-time to seconds and wrap all price
        and volume fields in ``Decimal`` for lossless arithmetic.
        """
        return Candle(
            symbol=symbol,
            timestamp=int(raw[_IDX_OPEN_TIME]) // _MS_PER_SECOND,
            open=Decimal(str(raw[_IDX_OPEN])),
            high=Decimal(str(raw[_IDX_HIGH])),
            low=Decimal(str(raw[_IDX_LOW])),
            close=Decimal(str(raw[_IDX_CLOSE])),
            volume=Decimal(str(raw[_IDX_VOLUME])),
            interval=interval,
        )
