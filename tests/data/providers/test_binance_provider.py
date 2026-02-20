"""Tests for Binance candle provider."""

from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock

import pytest

from trading_tools.core.models import Candle, Interval
from trading_tools.core.protocols import CandleProvider
from trading_tools.data.providers.binance import (
    _MAX_CANDLES_PER_REQUEST,
    BinanceCandleProvider,
    _symbol_to_binance,
)

EXPECTED_CANDLE_COUNT = 2
EXPECTED_API_CALLS_WITH_PAGINATION = 3
MS_PER_SECOND = 1000


def _mock_client(response_data: list[list[Any]]) -> AsyncMock:
    """Create a mock client returning fixed kline arrays."""
    client = AsyncMock()
    client.get = AsyncMock(return_value=response_data)
    return client


def _raw_kline(open_time_ms: int = 1_000_000, close: str = "100") -> list[Any]:
    """Create a raw kline array matching the Binance API format.

    Binance returns: [open_time, open, high, low, close, volume, close_time, ...]
    """
    return [
        open_time_ms,
        "95",
        "110",
        "90",
        close,
        "50",
        open_time_ms + 3_600_000,  # close_time
        "47500.00",  # quote asset volume
        100,  # number of trades
        "25.00",  # taker buy base
        "23750.00",  # taker buy quote
        "0",  # ignore
    ]


class TestSymbolConversion:
    """Tests for symbol format conversion."""

    def test_btc_usd_to_btcusdt(self) -> None:
        """Convert BTC-USD to BTCUSDT."""
        assert _symbol_to_binance("BTC-USD") == "BTCUSDT"

    def test_eth_usd_to_ethusdt(self) -> None:
        """Convert ETH-USD to ETHUSDT."""
        assert _symbol_to_binance("ETH-USD") == "ETHUSDT"

    def test_already_usdt_unchanged(self) -> None:
        """Leave USDT suffix unchanged."""
        assert _symbol_to_binance("BTC-USDT") == "BTCUSDT"

    def test_no_hyphen(self) -> None:
        """Handle symbols without a hyphen."""
        assert _symbol_to_binance("BTCUSD") == "BTCUSDT"


class TestBinanceCandleProvider:
    """Tests for BinanceCandleProvider."""

    def test_satisfies_protocol(self) -> None:
        """Test that BinanceCandleProvider satisfies CandleProvider protocol."""
        provider = BinanceCandleProvider(AsyncMock())
        assert isinstance(provider, CandleProvider)

    @pytest.mark.asyncio
    async def test_get_candles(self) -> None:
        """Test fetching and parsing candles from the API."""
        client = _mock_client(
            [
                _raw_kline(1_000_000, "100"),
                _raw_kline(2_000_000, "110"),
            ]
        )
        provider = BinanceCandleProvider(client)
        candles = await provider.get_candles("BTC-USD", Interval.H1, 0, 5)

        assert len(candles) == EXPECTED_CANDLE_COUNT
        assert all(isinstance(c, Candle) for c in candles)
        assert candles[0].close == Decimal(100)
        assert candles[1].close == Decimal(110)
        assert candles[0].interval == Interval.H1

    @pytest.mark.asyncio
    async def test_passes_correct_params(self) -> None:
        """Test that correct API parameters are passed."""
        client = _mock_client([])
        provider = BinanceCandleProvider(client)
        await provider.get_candles("ETH-USD", Interval.M5, 1, 2)

        client.get.assert_called_once_with(
            "/api/v3/klines",
            params={
                "symbol": "ETHUSDT",
                "interval": "5m",
                "startTime": 1 * MS_PER_SECOND,
                "endTime": 2 * MS_PER_SECOND,
                "limit": _MAX_CANDLES_PER_REQUEST,
            },
        )

    @pytest.mark.asyncio
    async def test_empty_response(self) -> None:
        """Test handling of empty API response."""
        client = _mock_client([])
        provider = BinanceCandleProvider(client)
        candles = await provider.get_candles("BTC-USD", Interval.H1, 0, 5)
        assert candles == []

    @pytest.mark.asyncio
    async def test_decimal_precision(self) -> None:
        """Test that decimal values maintain precision."""
        raw = _raw_kline(1_000_000, "29150.00")
        raw[1] = "29145.50"  # open
        raw[2] = "29200.75"  # high
        raw[3] = "29100.25"  # low
        raw[5] = "1.5432"  # volume
        client = _mock_client([raw])
        provider = BinanceCandleProvider(client)
        candles = await provider.get_candles("BTC-USD", Interval.H1, 0, 5)
        assert candles[0].close == Decimal("29150.00")
        assert candles[0].volume == Decimal("1.5432")

    @pytest.mark.asyncio
    async def test_interval_mapping(self) -> None:
        """Test interval-to-Binance-string mapping for all supported intervals."""
        client = _mock_client([])
        provider = BinanceCandleProvider(client)

        expected_binance = [
            (Interval.M1, "1m"),
            (Interval.M5, "5m"),
            (Interval.M15, "15m"),
            (Interval.H1, "1h"),
            (Interval.H4, "4h"),
            (Interval.D1, "1d"),
            (Interval.W1, "1w"),
        ]
        for interval, expected in expected_binance:
            await provider.get_candles("BTC-USD", interval, 0, 1)
            call_params = client.get.call_args[1]["params"]
            assert call_params["interval"] == expected

    @pytest.mark.asyncio
    async def test_pagination(self) -> None:
        """Test that pagination fetches multiple pages of candles."""
        page_size = _MAX_CANDLES_PER_REQUEST
        page1 = [_raw_kline(open_time_ms=i * MS_PER_SECOND) for i in range(page_size)]
        page2 = [_raw_kline(open_time_ms=(page_size + i) * MS_PER_SECOND) for i in range(page_size)]

        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[page1, page2, []],
        )
        provider = BinanceCandleProvider(client)

        candles = await provider.get_candles("BTC-USD", Interval.H1, 0, 10_000_000)

        expected_total = page_size * 2
        assert len(candles) == expected_total
        assert client.get.call_count == EXPECTED_API_CALLS_WITH_PAGINATION

    @pytest.mark.asyncio
    async def test_timestamp_converted_from_ms(self) -> None:
        """Test that candle timestamps are converted from ms to seconds."""
        start_ms = 1_704_067_200_000  # 2024-01-01 00:00:00 UTC in ms
        expected_ts = 1_704_067_200
        client = _mock_client([_raw_kline(open_time_ms=start_ms)])
        provider = BinanceCandleProvider(client)
        candles = await provider.get_candles("BTC-USD", Interval.H1, 0, 2_000_000)
        assert candles[0].timestamp == expected_ts

    @pytest.mark.asyncio
    async def test_symbol_preserved_in_output(self) -> None:
        """Test that the user-facing symbol is preserved in candle output."""
        client = _mock_client([_raw_kline()])
        provider = BinanceCandleProvider(client)
        candles = await provider.get_candles("BTC-USD", Interval.H1, 0, 5)
        assert candles[0].symbol == "BTC-USD"
