"""Tests for the CachedProvider."""

import pytest

from trading_tools.apps.backtester._providers import CachedProvider
from trading_tools.core.models import Candle, Decimal, Interval

_CANDLE = Candle(
    symbol="BTC-USD",
    timestamp=1000,
    open=Decimal(100),
    high=Decimal(110),
    low=Decimal(90),
    close=Decimal(105),
    volume=Decimal(50),
    interval=Interval.H1,
)


class TestCachedProvider:
    """Tests for CachedProvider."""

    @pytest.mark.asyncio
    async def test_returns_preloaded_candles(self) -> None:
        """Return the exact candles passed at construction."""
        candles = [_CANDLE]
        provider = CachedProvider(candles)
        result = await provider.get_candles("ETH-USD", Interval.M5, 0, 9999)
        assert result is candles

    @pytest.mark.asyncio
    async def test_ignores_parameters(self) -> None:
        """Return the same candles regardless of arguments."""
        provider = CachedProvider([_CANDLE, _CANDLE])
        result = await provider.get_candles("FOO", Interval.D1, 123, 456)
        expected_count = 2
        assert len(result) == expected_count
