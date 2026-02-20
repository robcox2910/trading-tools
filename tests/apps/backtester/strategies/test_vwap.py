"""Tests for VWAP strategy."""

from decimal import Decimal

import pytest

from trading_tools.apps.backtester.strategies.vwap import VwapStrategy
from trading_tools.core.models import Candle, Interval, Side, Signal
from trading_tools.core.protocols import TradingStrategy


def _candle(ts: int, close: str, volume: str = "10") -> Candle:
    """Create a candle with the given close price and volume."""
    c = Decimal(close)
    return Candle(
        symbol="BTC-USD",
        timestamp=ts,
        open=c,
        high=c,
        low=c,
        close=c,
        volume=Decimal(volume),
        interval=Interval.H1,
    )


class TestVwapStrategy:
    """Tests for VwapStrategy."""

    def test_satisfies_protocol(self) -> None:
        """Test that VwapStrategy satisfies TradingStrategy protocol."""
        assert isinstance(VwapStrategy(), TradingStrategy)

    def test_name_format(self) -> None:
        """Test strategy name format."""
        s = VwapStrategy(period=20)
        assert s.name == "vwap_20"

    def test_invalid_period(self) -> None:
        """Test that period < 2 raises ValueError."""
        with pytest.raises(ValueError, match="period must be >= 2"):
            VwapStrategy(period=1)

    def test_no_signal_insufficient_history(self) -> None:
        """Test no signal during warmup period."""
        s = VwapStrategy(period=3)
        history: list[Candle] = []
        # Need period + 1 = 4 candles before first signal
        for i in range(3):
            candle = _candle(i, "100")
            assert s.on_candle(candle, history) is None
            history.append(candle)

    def test_buy_signal_price_below_vwap(self) -> None:
        """Test buy signal when price crosses below VWAP."""
        s = VwapStrategy(period=3)
        # High-volume candles at 100, then price drops below VWAP
        prices_vols = [
            ("100", "100"),
            ("100", "100"),
            ("100", "100"),
            ("100", "10"),  # VWAP still ~100, prev at 100
            ("95", "10"),  # close drops below VWAP
        ]
        history: list[Candle] = []
        signals: list[Signal] = []
        for i, (p, v) in enumerate(prices_vols):
            candle = _candle(i, p, volume=v)
            sig = s.on_candle(candle, history)
            if sig is not None:
                signals.append(sig)
            history.append(candle)
        buy_signals = [sig for sig in signals if sig.side == Side.BUY]
        assert len(buy_signals) > 0
        assert "VWAP" in buy_signals[0].reason

    def test_sell_signal_price_above_vwap(self) -> None:
        """Test sell signal when price crosses above VWAP."""
        s = VwapStrategy(period=3)
        # Low price with high volume, then price spikes above VWAP
        prices_vols = [
            ("100", "100"),
            ("100", "100"),
            ("100", "100"),
            ("100", "10"),
            ("110", "10"),  # price spikes above VWAP
        ]
        history: list[Candle] = []
        signals: list[Signal] = []
        for i, (p, v) in enumerate(prices_vols):
            candle = _candle(i, p, volume=v)
            sig = s.on_candle(candle, history)
            if sig is not None:
                signals.append(sig)
            history.append(candle)
        sell_signals = [sig for sig in signals if sig.side == Side.SELL]
        assert len(sell_signals) > 0

    def test_no_signal_when_flat(self) -> None:
        """Test no signal when prices are flat."""
        s = VwapStrategy(period=3)
        history: list[Candle] = []
        for i in range(10):
            candle = _candle(i, "100")
            assert s.on_candle(candle, history) is None
            history.append(candle)
