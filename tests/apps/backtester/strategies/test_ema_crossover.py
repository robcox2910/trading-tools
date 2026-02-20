"""Tests for EMA crossover strategy."""

from decimal import Decimal

import pytest

from trading_tools.apps.backtester.strategies.ema_crossover import (
    EmaCrossoverStrategy,
)
from trading_tools.core.models import Candle, Interval, Side
from trading_tools.core.protocols import TradingStrategy


def _candle(ts: int, close: str) -> Candle:
    """Create a candle with the given timestamp and close price."""
    c = Decimal(close)
    return Candle(
        symbol="BTC-USD",
        timestamp=ts,
        open=c,
        high=c,
        low=c,
        close=c,
        volume=Decimal(10),
        interval=Interval.H1,
    )


class TestEmaCrossoverStrategy:
    """Tests for EmaCrossoverStrategy."""

    def test_satisfies_protocol(self) -> None:
        """Test that EmaCrossoverStrategy satisfies TradingStrategy protocol."""
        assert isinstance(EmaCrossoverStrategy(2, 3), TradingStrategy)

    def test_name_format(self) -> None:
        """Test strategy name format."""
        s = EmaCrossoverStrategy(10, 20)
        assert s.name == "ema_crossover_10_20"

    def test_invalid_periods(self) -> None:
        """Test that invalid period combinations raise ValueError."""
        with pytest.raises(ValueError, match="short_period"):
            EmaCrossoverStrategy(20, 10)
        with pytest.raises(ValueError, match="short_period"):
            EmaCrossoverStrategy(10, 10)

    def test_no_signal_insufficient_history(self) -> None:
        """Test no signal when history is shorter than required."""
        s = EmaCrossoverStrategy(2, 3)
        candle = _candle(1000, "100")
        assert s.on_candle(candle, []) is None
        assert s.on_candle(candle, [_candle(0, "100")]) is None
        assert s.on_candle(candle, [_candle(0, "100"), _candle(1, "100")]) is None

    def test_buy_signal_on_crossover(self) -> None:
        """Test buy signal when short EMA crosses above long EMA."""
        s = EmaCrossoverStrategy(2, 3)
        # Prices: stable then a sharp upward move
        prices = ["100", "100", "100", "120"]
        candles = [_candle(i, p) for i, p in enumerate(prices)]
        signal = s.on_candle(candles[-1], candles[:-1])
        assert signal is not None
        assert signal.side == Side.BUY
        assert "EMA2" in signal.reason
        assert "EMA3" in signal.reason

    def test_sell_signal_on_crossover(self) -> None:
        """Test sell signal when short EMA crosses below long EMA."""
        s = EmaCrossoverStrategy(2, 3)
        # Prices rising then sharp drop
        prices = ["100", "110", "115", "90"]
        candles = [_candle(i, p) for i, p in enumerate(prices)]
        signal = s.on_candle(candles[-1], candles[:-1])
        assert signal is not None
        assert signal.side == Side.SELL

    def test_no_signal_when_flat(self) -> None:
        """Test no signal when EMAs do not cross."""
        s = EmaCrossoverStrategy(2, 3)
        prices = ["100"] * 6
        candles = [_candle(i, p) for i, p in enumerate(prices)]
        for i in range(1, len(candles)):
            assert s.on_candle(candles[i], candles[:i]) is None
