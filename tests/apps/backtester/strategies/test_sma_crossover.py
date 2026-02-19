"""Tests for SMA crossover strategy."""

from decimal import Decimal

import pytest

from trading_tools.apps.backtester.strategies.sma_crossover import (
    SmaCrossoverStrategy,
)
from trading_tools.core.models import Candle, Interval, Side
from trading_tools.core.protocols import TradingStrategy


def _candle(ts: int, close: str) -> Candle:
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


class TestSmaCrossoverStrategy:
    """Tests for SmaCrossoverStrategy."""

    def test_satisfies_protocol(self) -> None:
        """Test that SmaCrossoverStrategy satisfies TradingStrategy protocol."""
        assert isinstance(SmaCrossoverStrategy(2, 3), TradingStrategy)

    def test_name(self) -> None:
        """Test strategy name format."""
        s = SmaCrossoverStrategy(10, 20)
        assert s.name == "sma_crossover_10_20"

    def test_invalid_periods(self) -> None:
        """Test that invalid period combinations raise ValueError."""
        with pytest.raises(ValueError, match="short_period"):
            SmaCrossoverStrategy(20, 10)
        with pytest.raises(ValueError, match="short_period"):
            SmaCrossoverStrategy(10, 10)

    def test_no_signal_insufficient_history(self) -> None:
        """Test no signal when history is shorter than required."""
        s = SmaCrossoverStrategy(2, 3)
        # Need long_period + 1 = 4 candles total (history + current)
        candle = _candle(1000, "100")
        assert s.on_candle(candle, []) is None
        assert s.on_candle(candle, [_candle(0, "100")]) is None
        assert s.on_candle(candle, [_candle(0, "100"), _candle(1, "100")]) is None

    def test_buy_signal_on_crossover(self) -> None:
        """Test buy signal when short SMA crosses above long SMA."""
        # short_period=2, long_period=3, need 4 candles
        # Prices: 100, 100, 100, 120
        # At candle 4 (idx 3): short_sma(2) of [100,120]=110, long_sma(3) of [100,100,120]=106.67
        # At candle 3 (idx 2): short_sma(2) of [100,100]=100, long_sma(3) of [100,100,100]=100
        # prev: short=100 <= long=100, current: short=110 > long=106.67 -> BUY
        s = SmaCrossoverStrategy(2, 3)
        history = [_candle(1, "100"), _candle(2, "100"), _candle(3, "100")]
        signal = s.on_candle(_candle(4, "120"), history)
        assert signal is not None
        assert signal.side == Side.BUY

    def test_sell_signal_on_crossover(self) -> None:
        """Test sell signal when short SMA crosses below long SMA."""
        # Prices: 100, 120, 120, 90
        # At candle 4: short_sma(2) of [120,90]=105, long_sma(3) of [120,120,90]=110
        # At candle 3: short_sma(2) of [100,120]=110, long_sma(3) of [100,120,120]=113.33
        # prev: short=110 < long=113.33 => not >=, no sell
        # Let me pick better numbers:
        # Prices: 100, 110, 115, 90
        # At candle 4: short(2) of [115,90]=102.5, long(3) of [110,115,90]=105
        # At candle 3: short(2) of [110,115]=112.5, long(3) of [100,110,115]=108.33
        # prev: short=112.5 >= long=108.33, current: short=102.5 < long=105 -> SELL
        s = SmaCrossoverStrategy(2, 3)
        history = [_candle(1, "100"), _candle(2, "110"), _candle(3, "115")]
        signal = s.on_candle(_candle(4, "90"), history)
        assert signal is not None
        assert signal.side == Side.SELL

    def test_no_signal_when_no_crossover(self) -> None:
        """Test no signal when SMAs do not cross."""
        # All same price -> no crossover
        s = SmaCrossoverStrategy(2, 3)
        history = [_candle(1, "100"), _candle(2, "100"), _candle(3, "100")]
        signal = s.on_candle(_candle(4, "100"), history)
        assert signal is None

    def test_signal_contains_reason(self) -> None:
        """Test that signal reason includes SMA period labels."""
        s = SmaCrossoverStrategy(2, 3)
        history = [_candle(1, "100"), _candle(2, "100"), _candle(3, "100")]
        signal = s.on_candle(_candle(4, "120"), history)
        assert signal is not None
        assert "SMA2" in signal.reason
        assert "SMA3" in signal.reason
