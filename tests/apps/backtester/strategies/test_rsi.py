"""Tests for RSI mean-reversion strategy."""

from decimal import Decimal

import pytest

from trading_tools.apps.backtester.strategies.rsi import RsiStrategy
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


class TestRsiStrategy:
    """Tests for RsiStrategy."""

    def test_satisfies_protocol(self) -> None:
        """Test that RsiStrategy satisfies TradingStrategy protocol."""
        assert isinstance(RsiStrategy(period=5), TradingStrategy)

    def test_name_format(self) -> None:
        """Test strategy name format."""
        s = RsiStrategy(period=14, oversold=30, overbought=70)
        assert s.name == "rsi_14_30_70"

    def test_invalid_period(self) -> None:
        """Test that period < 2 raises ValueError."""
        with pytest.raises(ValueError, match="period must be >= 2"):
            RsiStrategy(period=1)

    def test_invalid_thresholds(self) -> None:
        """Test that invalid threshold combinations raise ValueError."""
        with pytest.raises(ValueError, match="oversold"):
            RsiStrategy(oversold=70, overbought=30)
        with pytest.raises(ValueError, match="oversold"):
            RsiStrategy(oversold=0, overbought=70)

    def test_no_signal_insufficient_history(self) -> None:
        """Test no signal when history is shorter than required."""
        s = RsiStrategy(period=3)
        # Need period + 1 + 1 = 5 candles to compute current + previous RSI
        candles = [_candle(i, "100") for i in range(4)]
        assert s.on_candle(candles[-1], candles[:-1]) is None

    def test_buy_signal_on_oversold(self) -> None:
        """Test buy signal when RSI drops below oversold threshold."""
        s = RsiStrategy(period=3, oversold=30, overbought=70)
        # Create prices that cause RSI to drop below 30:
        # Start stable then drop sharply
        prices = ["100", "100", "100", "100", "90", "80"]
        candles = [_candle(i, p) for i, p in enumerate(prices)]
        # Feed candles one by one, collect signals
        signals: list[Signal] = []
        for i in range(1, len(candles)):
            sig = s.on_candle(candles[i], candles[:i])
            if sig is not None:
                signals.append(sig)
        buy_signals = [sig for sig in signals if sig.side == Side.BUY]
        assert len(buy_signals) > 0
        assert "RSI(3)" in buy_signals[0].reason

    def test_sell_signal_on_overbought(self) -> None:
        """Test sell signal when RSI rises above overbought threshold."""
        s = RsiStrategy(period=3, oversold=30, overbought=70)
        # Prices dip first (keeping RSI moderate) then spike (crossing above 70)
        prices = ["100", "98", "96", "102", "110"]
        candles = [_candle(i, p) for i, p in enumerate(prices)]
        signals: list[Signal] = []
        for i in range(1, len(candles)):
            sig = s.on_candle(candles[i], candles[:i])
            if sig is not None:
                signals.append(sig)
        sell_signals = [sig for sig in signals if sig.side == Side.SELL]
        assert len(sell_signals) > 0
        assert "RSI(3)" in sell_signals[0].reason

    def test_no_signal_when_flat(self) -> None:
        """Test no signal when prices are flat."""
        s = RsiStrategy(period=3, oversold=30, overbought=70)
        prices = ["100"] * 8
        candles = [_candle(i, p) for i, p in enumerate(prices)]
        for i in range(1, len(candles)):
            assert s.on_candle(candles[i], candles[:i]) is None
