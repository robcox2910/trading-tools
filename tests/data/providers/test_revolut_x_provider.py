"""Tests for Revolut X candle provider."""

from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock

import pytest

from trading_tools.core.models import Candle, Interval
from trading_tools.core.protocols import CandleProvider
from trading_tools.data.providers.revolut_x import RevolutXCandleProvider


def _mock_client(response_data: list[dict[str, Any]]) -> AsyncMock:
    client = AsyncMock()
    client.get = AsyncMock(return_value={"data": response_data})
    return client


def _raw_candle(ts: int = 1000, close: str = "100") -> dict[str, Any]:
    return {
        "timestamp": ts,
        "open": "95",
        "high": "110",
        "low": "90",
        "close": close,
        "volume": "50",
    }


class TestRevolutXCandleProvider:
    def test_satisfies_protocol(self) -> None:
        provider = RevolutXCandleProvider(AsyncMock())
        assert isinstance(provider, CandleProvider)

    @pytest.mark.asyncio
    async def test_get_candles(self) -> None:
        client = _mock_client([_raw_candle(1000, "100"), _raw_candle(2000, "110")])
        provider = RevolutXCandleProvider(client)
        candles = await provider.get_candles("BTC-USD", Interval.H1, 0, 5000)

        assert len(candles) == 2
        assert all(isinstance(c, Candle) for c in candles)
        assert candles[0].close == Decimal("100")
        assert candles[1].close == Decimal("110")
        assert candles[0].interval == Interval.H1

    @pytest.mark.asyncio
    async def test_passes_correct_params(self) -> None:
        client = _mock_client([])
        provider = RevolutXCandleProvider(client)
        await provider.get_candles("ETH-USD", Interval.M5, 1000, 2000)

        client.get.assert_called_once_with(
            "/exchange/candles",
            params={
                "symbol": "ETH-USD",
                "granularity": "300",
                "start": "1000",
                "end": "2000",
            },
        )

    @pytest.mark.asyncio
    async def test_empty_response(self) -> None:
        client = _mock_client([])
        provider = RevolutXCandleProvider(client)
        candles = await provider.get_candles("BTC-USD", Interval.H1, 0, 5000)
        assert candles == []

    @pytest.mark.asyncio
    async def test_decimal_precision(self) -> None:
        raw = {
            "timestamp": 1000,
            "open": "29145.50",
            "high": "29200.75",
            "low": "29100.25",
            "close": "29150.00",
            "volume": "1.5432",
        }
        client = _mock_client([raw])
        provider = RevolutXCandleProvider(client)
        candles = await provider.get_candles("BTC-USD", Interval.H1, 0, 5000)
        assert candles[0].close == Decimal("29150.00")
        assert candles[0].volume == Decimal("1.5432")

    @pytest.mark.asyncio
    async def test_interval_mapping(self) -> None:
        client = _mock_client([])
        provider = RevolutXCandleProvider(client)

        for interval, expected_gran in [
            (Interval.M1, "60"),
            (Interval.M15, "900"),
            (Interval.H4, "14400"),
            (Interval.D1, "86400"),
            (Interval.W1, "604800"),
        ]:
            await provider.get_candles("BTC-USD", interval, 0, 1000)
            call_params = client.get.call_args[1]["params"]
            assert call_params["granularity"] == expected_gran
