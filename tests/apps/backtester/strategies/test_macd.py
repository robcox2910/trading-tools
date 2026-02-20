"""Tests for MACD strategy."""

from decimal import Decimal

import pytest

from trading_tools.apps.backtester.strategies.macd import MacdStrategy
from trading_tools.core.models import Candle, Interval, Side, Signal
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


class TestMacdStrategy:
    """Tests for MacdStrategy."""

    def test_satisfies_protocol(self) -> None:
        """Test that MacdStrategy satisfies TradingStrategy protocol."""
        assert isinstance(
            MacdStrategy(fast_period=3, slow_period=5, signal_period=2), TradingStrategy
        )

    def test_name_format(self) -> None:
        """Test strategy name format."""
        s = MacdStrategy(fast_period=12, slow_period=26, signal_period=9)
        assert s.name == "macd_12_26_9"

    def test_invalid_fast_slow(self) -> None:
        """Test that fast_period >= slow_period raises ValueError."""
        with pytest.raises(ValueError, match="fast_period"):
            MacdStrategy(fast_period=26, slow_period=12)
        with pytest.raises(ValueError, match="fast_period"):
            MacdStrategy(fast_period=12, slow_period=12)

    def test_invalid_signal_period(self) -> None:
        """Test that signal_period < 1 raises ValueError."""
        with pytest.raises(ValueError, match="signal_period"):
            MacdStrategy(signal_period=0)

    def test_no_signal_insufficient_history(self) -> None:
        """Test no signal when history is shorter than required warmup."""
        s = MacdStrategy(fast_period=3, slow_period=5, signal_period=2)
        # Need slow_period + signal_period + 1 = 8 candles
        candles = [_candle(i, "100") for i in range(7)]
        assert s.on_candle(candles[-1], candles[:-1]) is None

    def test_buy_signal_on_crossover(self) -> None:
        """Test buy signal when MACD crosses above signal line."""
        s = MacdStrategy(fast_period=3, slow_period=5, signal_period=2)
        # Start flat, then sharp rise to push fast EMA above slow EMA
        prices = ["100"] * 7 + ["100", "110", "120", "130"]
        candles = [_candle(i, p) for i, p in enumerate(prices)]
        signals: list[Signal] = []
        for i in range(1, len(candles)):
            sig = s.on_candle(candles[i], candles[:i])
            if sig is not None:
                signals.append(sig)
        buy_signals = [sig for sig in signals if sig.side == Side.BUY]
        assert len(buy_signals) > 0
        assert "MACD" in buy_signals[0].reason

    def test_sell_signal_on_crossover(self) -> None:
        """Test sell signal when MACD crosses below signal line."""
        s = MacdStrategy(fast_period=3, slow_period=5, signal_period=2)
        # Rise first then sharp drop
        prices = ["100"] * 5 + ["110", "120", "130", "120", "100", "80"]
        candles = [_candle(i, p) for i, p in enumerate(prices)]
        signals: list[Signal] = []
        for i in range(1, len(candles)):
            sig = s.on_candle(candles[i], candles[:i])
            if sig is not None:
                signals.append(sig)
        sell_signals = [sig for sig in signals if sig.side == Side.SELL]
        assert len(sell_signals) > 0

    def test_no_signal_when_flat(self) -> None:
        """Test no signal when prices are completely flat."""
        s = MacdStrategy(fast_period=3, slow_period=5, signal_period=2)
        prices = ["100"] * 12
        candles = [_candle(i, p) for i, p in enumerate(prices)]
        for i in range(1, len(candles)):
            assert s.on_candle(candles[i], candles[:i]) is None
