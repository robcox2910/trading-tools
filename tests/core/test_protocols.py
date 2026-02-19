"""Tests for core protocols."""

from decimal import Decimal

import pytest

from trading_tools.core.models import Candle, Interval, Signal
from trading_tools.core.protocols import CandleProvider, TradingStrategy


class FakeProvider:
    """A class that structurally satisfies CandleProvider."""

    async def get_candles(
        self,
        symbol: str,  # noqa: ARG002
        interval: Interval,  # noqa: ARG002
        start_ts: int,  # noqa: ARG002
        end_ts: int,  # noqa: ARG002
    ) -> list[Candle]:
        """Return empty candle list."""
        return []


class FakeStrategy:
    """A class that structurally satisfies TradingStrategy."""

    @property
    def name(self) -> str:
        """Return strategy name."""
        return "fake"

    def on_candle(self, candle: Candle, history: list[Candle]) -> Signal | None:  # noqa: ARG002
        """Return no signal."""
        return None


class BadProvider:
    """Missing get_candles method."""


class BadStrategy:
    """Missing required methods."""

    @property
    def name(self) -> str:
        """Return strategy name."""
        return "bad"


class TestCandleProvider:
    """Tests for CandleProvider protocol."""

    def test_structural_match(self) -> None:
        """Test that FakeProvider satisfies CandleProvider."""
        assert isinstance(FakeProvider(), CandleProvider)

    def test_structural_mismatch(self) -> None:
        """Test that BadProvider does not satisfy CandleProvider."""
        assert not isinstance(BadProvider(), CandleProvider)

    @pytest.mark.asyncio
    async def test_returns_candles(self) -> None:
        """Test that provider returns candle list."""
        provider: CandleProvider = FakeProvider()
        result = await provider.get_candles("BTC-USD", Interval.H1, 0, 1000)
        assert result == []


class TestTradingStrategy:
    """Tests for TradingStrategy protocol."""

    def test_structural_match(self) -> None:
        """Test that FakeStrategy satisfies TradingStrategy."""
        assert isinstance(FakeStrategy(), TradingStrategy)

    def test_structural_mismatch(self) -> None:
        """Test that BadStrategy does not satisfy TradingStrategy."""
        assert not isinstance(BadStrategy(), TradingStrategy)

    def test_on_candle(self) -> None:
        """Test strategy on_candle returns None for fake strategy."""
        strategy: TradingStrategy = FakeStrategy()
        candle = Candle(
            symbol="BTC-USD",
            timestamp=1000,
            open=Decimal(100),
            high=Decimal(110),
            low=Decimal(90),
            close=Decimal(105),
            volume=Decimal(50),
            interval=Interval.H1,
        )
        assert strategy.on_candle(candle, []) is None
        assert strategy.name == "fake"
