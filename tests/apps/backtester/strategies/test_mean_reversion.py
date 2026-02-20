"""Tests for mean reversion strategy."""

from decimal import Decimal

import pytest

from trading_tools.apps.backtester.strategies.mean_reversion import (
    MeanReversionStrategy,
)
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


class TestMeanReversionStrategy:
    """Tests for MeanReversionStrategy."""

    def test_satisfies_protocol(self) -> None:
        """Test that MeanReversionStrategy satisfies TradingStrategy protocol."""
        assert isinstance(MeanReversionStrategy(), TradingStrategy)

    def test_name_format(self) -> None:
        """Test strategy name format."""
        s = MeanReversionStrategy(period=20, z_threshold=2.0)
        assert s.name == "mean_reversion_20_2.0"

    def test_invalid_period(self) -> None:
        """Test that period < 2 raises ValueError."""
        with pytest.raises(ValueError, match="period must be >= 2"):
            MeanReversionStrategy(period=1)

    def test_invalid_z_threshold(self) -> None:
        """Test that z_threshold <= 0 raises ValueError."""
        with pytest.raises(ValueError, match="z_threshold"):
            MeanReversionStrategy(z_threshold=0)
        with pytest.raises(ValueError, match="z_threshold"):
            MeanReversionStrategy(z_threshold=-1.0)

    def test_no_signal_insufficient_history(self) -> None:
        """Test no signal during warmup period."""
        s = MeanReversionStrategy(period=3)
        history: list[Candle] = []
        for i in range(3):
            candle = _candle(i, "100")
            assert s.on_candle(candle, history) is None
            history.append(candle)

    def test_buy_signal_low_z_score(self) -> None:
        """Test buy signal when z-score drops below -threshold."""
        s = MeanReversionStrategy(period=5, z_threshold=1.5)
        # Stable prices then sharp drop to create negative z-score
        prices = ["100", "100", "100", "100", "100", "100", "80"]
        history: list[Candle] = []
        signals: list[Signal] = []
        for i, p in enumerate(prices):
            candle = _candle(i, p)
            sig = s.on_candle(candle, history)
            if sig is not None:
                signals.append(sig)
            history.append(candle)
        buy_signals = [sig for sig in signals if sig.side == Side.BUY]
        assert len(buy_signals) > 0
        assert "Z-score" in buy_signals[0].reason

    def test_sell_signal_high_z_score(self) -> None:
        """Test sell signal when z-score rises above +threshold."""
        s = MeanReversionStrategy(period=5, z_threshold=1.5)
        # Stable prices then sharp spike
        prices = ["100", "100", "100", "100", "100", "100", "120"]
        history: list[Candle] = []
        signals: list[Signal] = []
        for i, p in enumerate(prices):
            candle = _candle(i, p)
            sig = s.on_candle(candle, history)
            if sig is not None:
                signals.append(sig)
            history.append(candle)
        sell_signals = [sig for sig in signals if sig.side == Side.SELL]
        assert len(sell_signals) > 0

    def test_no_signal_when_flat(self) -> None:
        """Test no signal when prices are flat."""
        s = MeanReversionStrategy(period=3)
        history: list[Candle] = []
        for i in range(10):
            candle = _candle(i, "100")
            assert s.on_candle(candle, history) is None
            history.append(candle)
