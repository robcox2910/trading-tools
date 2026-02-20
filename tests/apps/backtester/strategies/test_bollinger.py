"""Tests for Bollinger Band breakout strategy."""

from decimal import Decimal

import pytest

from trading_tools.apps.backtester.strategies.bollinger import BollingerStrategy
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


class TestBollingerStrategy:
    """Tests for BollingerStrategy."""

    def test_satisfies_protocol(self) -> None:
        """Test that BollingerStrategy satisfies TradingStrategy protocol."""
        assert isinstance(BollingerStrategy(period=3), TradingStrategy)

    def test_name_format(self) -> None:
        """Test strategy name format."""
        s = BollingerStrategy(period=20, num_std=2.0)
        assert s.name == "bollinger_20_2.0"

    def test_invalid_period(self) -> None:
        """Test that period < 2 raises ValueError."""
        with pytest.raises(ValueError, match="period must be >= 2"):
            BollingerStrategy(period=1)

    def test_invalid_num_std(self) -> None:
        """Test that non-positive num_std raises ValueError."""
        with pytest.raises(ValueError, match="num_std must be > 0"):
            BollingerStrategy(num_std=0)
        with pytest.raises(ValueError, match="num_std must be > 0"):
            BollingerStrategy(num_std=-1.0)

    def test_no_signal_insufficient_history(self) -> None:
        """Test no signal when history is shorter than required."""
        s = BollingerStrategy(period=3)
        # Need period + 1 = 4 candles total
        candles = [_candle(i, "100") for i in range(3)]
        assert s.on_candle(candles[-1], candles[:-1]) is None

    def test_buy_signal_above_upper_band(self) -> None:
        """Test buy signal when close crosses above upper band."""
        s = BollingerStrategy(period=3, num_std=1.0)
        # Stable prices then a big spike
        prices = ["100", "100", "100", "100", "130"]
        candles = [_candle(i, p) for i, p in enumerate(prices)]
        signals: list[Signal] = []
        for i in range(1, len(candles)):
            sig = s.on_candle(candles[i], candles[:i])
            if sig is not None:
                signals.append(sig)
        buy_signals = [sig for sig in signals if sig.side == Side.BUY]
        assert len(buy_signals) > 0
        assert "upper" in buy_signals[0].reason.lower()

    def test_sell_signal_below_lower_band(self) -> None:
        """Test sell signal when close crosses below lower band."""
        s = BollingerStrategy(period=3, num_std=1.0)
        # Stable prices then a big drop
        prices = ["100", "100", "100", "100", "70"]
        candles = [_candle(i, p) for i, p in enumerate(prices)]
        signals: list[Signal] = []
        for i in range(1, len(candles)):
            sig = s.on_candle(candles[i], candles[:i])
            if sig is not None:
                signals.append(sig)
        sell_signals = [sig for sig in signals if sig.side == Side.SELL]
        assert len(sell_signals) > 0
        assert "lower" in sell_signals[0].reason.lower()

    def test_no_signal_when_flat(self) -> None:
        """Test no signal when prices stay within bands."""
        s = BollingerStrategy(period=3, num_std=2.0)
        prices = ["100"] * 6
        candles = [_candle(i, p) for i, p in enumerate(prices)]
        for i in range(1, len(candles)):
            assert s.on_candle(candles[i], candles[:i]) is None
