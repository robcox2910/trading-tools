"""Tests for Donchian Channel breakout strategy."""

from decimal import Decimal

import pytest

from trading_tools.apps.backtester.strategies.donchian import DonchianStrategy
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


class TestDonchianStrategy:
    """Tests for DonchianStrategy."""

    def test_satisfies_protocol(self) -> None:
        """Test that DonchianStrategy satisfies TradingStrategy protocol."""
        assert isinstance(DonchianStrategy(), TradingStrategy)

    def test_name_format(self) -> None:
        """Test strategy name format."""
        s = DonchianStrategy(period=20)
        assert s.name == "donchian_20"

    def test_invalid_period(self) -> None:
        """Test that period < 1 raises ValueError."""
        with pytest.raises(ValueError, match="period must be >= 1"):
            DonchianStrategy(period=0)

    def test_no_signal_insufficient_history(self) -> None:
        """Test no signal during warmup period."""
        s = DonchianStrategy(period=3)
        history: list[Candle] = []
        for i in range(3):
            candle = _candle(i, "100", high="105", low="95")
            assert s.on_candle(candle, history) is None
            history.append(candle)

    def test_buy_signal_upper_breakout(self) -> None:
        """Test buy signal when close breaks above the channel."""
        s = DonchianStrategy(period=3)
        # Build channel with highs at 105, then break above
        candles_data = [
            ("100", "105", "95"),
            ("100", "105", "95"),
            ("100", "105", "95"),
            ("110", "110", "100"),  # close 110 > upper channel 105
        ]
        history: list[Candle] = []
        signals: list[Signal] = []
        for i, (c, h, lo) in enumerate(candles_data):
            candle = _candle(i, c, high=h, low=lo)
            sig = s.on_candle(candle, history)
            if sig is not None:
                signals.append(sig)
            history.append(candle)
        buy_signals = [sig for sig in signals if sig.side == Side.BUY]
        assert len(buy_signals) == 1
        assert "Donchian" in buy_signals[0].reason

    def test_sell_signal_lower_breakout(self) -> None:
        """Test sell signal when close breaks below the channel."""
        s = DonchianStrategy(period=3)
        # Build channel with lows at 95, then break below
        candles_data = [
            ("100", "105", "95"),
            ("100", "105", "95"),
            ("100", "105", "95"),
            ("90", "100", "85"),  # close 90 < lower channel 95
        ]
        history: list[Candle] = []
        signals: list[Signal] = []
        for i, (c, h, lo) in enumerate(candles_data):
            candle = _candle(i, c, high=h, low=lo)
            sig = s.on_candle(candle, history)
            if sig is not None:
                signals.append(sig)
            history.append(candle)
        sell_signals = [sig for sig in signals if sig.side == Side.SELL]
        assert len(sell_signals) == 1

    def test_no_signal_within_channel(self) -> None:
        """Test no signal when price stays within the channel."""
        s = DonchianStrategy(period=3)
        # All candles within the same range
        history: list[Candle] = []
        for i in range(6):
            candle = _candle(i, "100", high="105", low="95")
            assert s.on_candle(candle, history) is None
            history.append(candle)
