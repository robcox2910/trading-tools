"""Tests for core protocols."""

from decimal import Decimal

import pytest

from trading_tools.core.models import Candle, Interval, Signal
from trading_tools.core.protocols import CandleProvider, TradingStrategy


class FakeProvider:
    """A class that structurally satisfies CandleProvider."""

    async def get_candles(
        self,
        symbol: str,
        interval: Interval,
        start_ts: int,
        end_ts: int,
    ) -> list[Candle]:
        return []


class FakeStrategy:
    """A class that structurally satisfies TradingStrategy."""

    @property
    def name(self) -> str:
        return "fake"

    def on_candle(self, candle: Candle, history: list[Candle]) -> Signal | None:
        return None


class BadProvider:
    """Missing get_candles method."""

    pass


class BadStrategy:
    """Missing required methods."""

    @property
    def name(self) -> str:
        return "bad"


class TestCandleProvider:
    def test_structural_match(self) -> None:
        assert isinstance(FakeProvider(), CandleProvider)

    def test_structural_mismatch(self) -> None:
        assert not isinstance(BadProvider(), CandleProvider)

    @pytest.mark.asyncio
    async def test_returns_candles(self) -> None:
        provider: CandleProvider = FakeProvider()
        result = await provider.get_candles("BTC-USD", Interval.H1, 0, 1000)
        assert result == []


class TestTradingStrategy:
    def test_structural_match(self) -> None:
        assert isinstance(FakeStrategy(), TradingStrategy)

    def test_structural_mismatch(self) -> None:
        assert not isinstance(BadStrategy(), TradingStrategy)

    def test_on_candle(self) -> None:
        strategy: TradingStrategy = FakeStrategy()
        candle = Candle(
            symbol="BTC-USD",
            timestamp=1000,
            open=Decimal("100"),
            high=Decimal("110"),
            low=Decimal("90"),
            close=Decimal("105"),
            volume=Decimal("50"),
            interval=Interval.H1,
        )
        assert strategy.on_candle(candle, []) is None
        assert strategy.name == "fake"
