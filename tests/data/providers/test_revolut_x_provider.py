"""Tests for Revolut X candle provider."""

from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock

import pytest

from trading_tools.core.models import Candle, Interval
from trading_tools.core.protocols import CandleProvider
from trading_tools.data.providers.revolut_x import (
    _MAX_CANDLES_PER_REQUEST,
    RevolutXCandleProvider,
)

EXPECTED_CANDLE_COUNT = 2
EXPECTED_API_CALLS_WITH_PAGINATION = 3
MS_PER_SECOND = 1000


def _mock_client(response_data: list[dict[str, Any]]) -> AsyncMock:
    """Create a mock client returning fixed response data."""
    client = AsyncMock()
    client.get = AsyncMock(return_value={"data": response_data})
    return client


def _raw_candle(start_ms: int = 1_000_000, close: str = "100") -> dict[str, Any]:
    """Create a raw candle dict matching the Revolut X API format."""
    return {
        "start": start_ms,
        "open": "95",
        "high": "110",
        "low": "90",
        "close": close,
        "volume": "50",
    }


class TestRevolutXCandleProvider:
    """Tests for RevolutXCandleProvider."""

    def test_satisfies_protocol(self) -> None:
        """Test that RevolutXCandleProvider satisfies CandleProvider protocol."""
        provider = RevolutXCandleProvider(AsyncMock())
        assert isinstance(provider, CandleProvider)

    @pytest.mark.asyncio
    async def test_get_candles(self) -> None:
        """Test fetching and parsing candles from the API."""
        client = _mock_client(
            [
                _raw_candle(1_000_000, "100"),
                _raw_candle(2_000_000, "110"),
            ]
        )
        provider = RevolutXCandleProvider(client)
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
        provider = RevolutXCandleProvider(client)
        await provider.get_candles("ETH-USD", Interval.M5, 1, 2)

        client.get.assert_called_once_with(
            "/candles/ETH-USD",
            params={
                "interval": 5,
                "since": 1 * MS_PER_SECOND,
                "until": 2 * MS_PER_SECOND,
            },
        )

    @pytest.mark.asyncio
    async def test_empty_response(self) -> None:
        """Test handling of empty API response."""
        client = _mock_client([])
        provider = RevolutXCandleProvider(client)
        candles = await provider.get_candles("BTC-USD", Interval.H1, 0, 5)
        assert candles == []

    @pytest.mark.asyncio
    async def test_decimal_precision(self) -> None:
        """Test that decimal values maintain precision."""
        raw = {
            "start": 1_000_000,
            "open": "29145.50",
            "high": "29200.75",
            "low": "29100.25",
            "close": "29150.00",
            "volume": "1.5432",
        }
        client = _mock_client([raw])
        provider = RevolutXCandleProvider(client)
        candles = await provider.get_candles("BTC-USD", Interval.H1, 0, 5)
        assert candles[0].close == Decimal("29150.00")
        assert candles[0].volume == Decimal("1.5432")

    @pytest.mark.asyncio
    async def test_interval_mapping(self) -> None:
        """Test interval-to-minutes mapping for all supported intervals."""
        client = _mock_client([])
        provider = RevolutXCandleProvider(client)

        expected_minutes = [
            (Interval.M5, 5),
            (Interval.M15, 15),
            (Interval.H1, 60),
            (Interval.H4, 240),
            (Interval.D1, 1440),
            (Interval.W1, 10080),
        ]
        for interval, expected in expected_minutes:
            await provider.get_candles("BTC-USD", interval, 0, 1)
            call_params = client.get.call_args[1]["params"]
            assert call_params["interval"] == expected

    @pytest.mark.asyncio
    async def test_m1_raises_value_error(self) -> None:
        """Test that M1 interval raises ValueError (unsupported by API)."""
        client = _mock_client([])
        provider = RevolutXCandleProvider(client)

        with pytest.raises(ValueError, match="not supported"):
            await provider.get_candles("BTC-USD", Interval.M1, 0, 1)

    @pytest.mark.asyncio
    async def test_pagination(self) -> None:
        """Test that pagination fetches multiple pages of candles."""
        page_size = _MAX_CANDLES_PER_REQUEST
        page1 = [_raw_candle(start_ms=i * MS_PER_SECOND) for i in range(page_size)]
        page2 = [_raw_candle(start_ms=(page_size + i) * MS_PER_SECOND) for i in range(page_size)]

        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[{"data": page1}, {"data": page2}, {"data": []}],
        )
        provider = RevolutXCandleProvider(client)

        candles = await provider.get_candles("BTC-USD", Interval.H1, 0, 1_000_000)

        expected_total = page_size * 2
        assert len(candles) == expected_total
        assert client.get.call_count == EXPECTED_API_CALLS_WITH_PAGINATION

    @pytest.mark.asyncio
    async def test_timestamp_converted_from_ms(self) -> None:
        """Test that candle timestamps are converted from ms to seconds."""
        start_ms = 1_704_067_200_000  # 2024-01-01 00:00:00 UTC in ms
        expected_ts = 1_704_067_200
        client = _mock_client([_raw_candle(start_ms=start_ms)])
        provider = RevolutXCandleProvider(client)
        candles = await provider.get_candles("BTC-USD", Interval.H1, 0, 2_000_000)
        assert candles[0].timestamp == expected_ts

    @pytest.mark.asyncio
    async def test_pagination_breaks_on_no_advancement(self) -> None:
        """Break pagination loop when API returns same timestamps repeatedly."""
        page_size = _MAX_CANDLES_PER_REQUEST
        # Return the same timestamps every time — should break out
        stuck_page = [_raw_candle(start_ms=i * MS_PER_SECOND) for i in range(page_size)]

        client = AsyncMock()
        client.get = AsyncMock(return_value={"data": stuck_page})
        provider = RevolutXCandleProvider(client)

        candles = await provider.get_candles("BTC-USD", Interval.H1, 0, 1_000_000)

        # First call advances; second call returns same last timestamp
        # so the guard breaks the loop after 2 iterations
        expected_pages = 2
        assert len(candles) == page_size * expected_pages
        assert client.get.call_count == expected_pages
