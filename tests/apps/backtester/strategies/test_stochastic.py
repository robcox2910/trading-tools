"""Tests for Stochastic oscillator strategy."""

from decimal import Decimal

import pytest

from trading_tools.apps.backtester.strategies.stochastic import StochasticStrategy
from trading_tools.core.models import Candle, Interval, Side, Signal
from trading_tools.core.protocols import TradingStrategy


def _candle(
    ts: int,
    close: str,
    high: str | None = None,
    low: str | None = None,
) -> Candle:
    """Create a candle with the given prices."""
    c = Decimal(close)
    return Candle(
        symbol="BTC-USD",
        timestamp=ts,
        open=c,
        high=Decimal(high) if high else c,
        low=Decimal(low) if low else c,
        close=c,
        volume=Decimal(10),
        interval=Interval.H1,
    )


class TestStochasticStrategy:
    """Tests for StochasticStrategy."""

    def test_satisfies_protocol(self) -> None:
        """Test that StochasticStrategy satisfies TradingStrategy protocol."""
        assert isinstance(StochasticStrategy(), TradingStrategy)

    def test_name_format(self) -> None:
        """Test strategy name format."""
        s = StochasticStrategy(k_period=14, d_period=3, oversold=20, overbought=80)
        assert s.name == "stochastic_14_3_20_80"

    def test_invalid_k_period(self) -> None:
        """Test that k_period < 1 raises ValueError."""
        with pytest.raises(ValueError, match="k_period"):
            StochasticStrategy(k_period=0)

    def test_invalid_d_period(self) -> None:
        """Test that d_period < 1 raises ValueError."""
        with pytest.raises(ValueError, match="d_period"):
            StochasticStrategy(d_period=0)

    def test_invalid_thresholds(self) -> None:
        """Test that invalid threshold combinations raise ValueError."""
        with pytest.raises(ValueError, match="oversold"):
            StochasticStrategy(oversold=80, overbought=20)

    def test_no_signal_insufficient_history(self) -> None:
        """Test no signal during warmup period."""
        s = StochasticStrategy(k_period=3, d_period=2)
        history: list[Candle] = []
        # Need k_period + d_period = 5 candles to produce first signal
        for i in range(4):
            candle = _candle(i, "100")
            assert s.on_candle(candle, history) is None
            history.append(candle)

    def test_buy_signal_in_oversold(self) -> None:
        """Test buy signal when %K crosses above %D in oversold zone."""
        s = StochasticStrategy(k_period=3, d_period=2, oversold=30, overbought=70)
        # Start high then drop sharply to push %K into oversold, then uptick
        prices_hlc = [
            ("110", "90", "100"),
            ("110", "90", "100"),
            ("110", "90", "92"),  # drop close to low end
            ("110", "90", "91"),  # further drop
            ("110", "90", "93"),  # slight uptick — %K should cross above %D
        ]
        history: list[Candle] = []
        signals: list[Signal] = []
        for i, (h, lo, c) in enumerate(prices_hlc):
            candle = _candle(i, c, high=h, low=lo)
            sig = s.on_candle(candle, history)
            if sig is not None:
                signals.append(sig)
            history.append(candle)
        buy_signals = [sig for sig in signals if sig.side == Side.BUY]
        assert len(buy_signals) > 0
        assert "%K" in buy_signals[0].reason

    def test_sell_signal_in_overbought(self) -> None:
        """Test sell signal when %K crosses below %D in overbought zone."""
        s = StochasticStrategy(k_period=3, d_period=2, oversold=20, overbought=70)
        # Push %K into overbought zone then slight downtick
        prices_hlc = [
            ("110", "90", "100"),
            ("110", "90", "100"),
            ("110", "90", "109"),  # near high
            ("110", "90", "110"),  # at high — %K = 100
            ("110", "90", "108"),  # downtick — %K drops, crosses below %D
        ]
        history: list[Candle] = []
        signals: list[Signal] = []
        for i, (h, lo, c) in enumerate(prices_hlc):
            candle = _candle(i, c, high=h, low=lo)
            sig = s.on_candle(candle, history)
            if sig is not None:
                signals.append(sig)
            history.append(candle)
        sell_signals = [sig for sig in signals if sig.side == Side.SELL]
        assert len(sell_signals) > 0

    def test_no_signal_when_flat(self) -> None:
        """Test no signal when prices are flat."""
        s = StochasticStrategy(k_period=3, d_period=2)
        history: list[Candle] = []
        for i in range(10):
            candle = _candle(i, "100")
            assert s.on_candle(candle, history) is None
            history.append(candle)
