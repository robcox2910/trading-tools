"""Tests for the late snipe strategy."""

from decimal import Decimal

import pytest

from trading_tools.apps.polymarket_bot.models import MarketSnapshot
from trading_tools.apps.polymarket_bot.protocols import PredictionMarketStrategy
from trading_tools.apps.polymarket_bot.strategies.late_snipe import (
    PMLateSnipeStrategy,
)
from trading_tools.clients.polymarket.models import OrderBook
from trading_tools.core.models import ZERO, Side

_CONDITION_ID = "cond_test"
_THRESHOLD = Decimal("0.90")
_WINDOW = 60


def _empty_book() -> OrderBook:
    """Create an empty order book for testing."""
    return OrderBook(token_id="tok1", bids=(), asks=(), spread=ZERO, midpoint=ZERO)


def _snap(
    ts: int,
    yes_price: str,
    no_price: str,
    end_date: str = "2026-02-22T12:05:00+00:00",
) -> MarketSnapshot:
    """Create a snapshot with given prices and end date.

    Args:
        ts: Unix epoch seconds.
        yes_price: YES token price as a string.
        no_price: NO token price as a string.
        end_date: ISO-8601 end date string.

    Returns:
        MarketSnapshot for testing.

    """
    return MarketSnapshot(
        condition_id=_CONDITION_ID,
        question="BTC Up or Down?",
        timestamp=ts,
        yes_price=Decimal(yes_price),
        no_price=Decimal(no_price),
        order_book=_empty_book(),
        volume=Decimal(1000),
        liquidity=Decimal(500),
        end_date=end_date,
    )


# End date: 2026-02-22T12:05:00 UTC = epoch 1771671900
_END_EPOCH = 1771761900


class TestPMLateSnipeStrategy:
    """Tests for PMLateSnipeStrategy."""

    def test_satisfies_protocol(self) -> None:
        """Test that the strategy satisfies PredictionMarketStrategy protocol."""
        assert isinstance(PMLateSnipeStrategy(), PredictionMarketStrategy)

    def test_name_format(self) -> None:
        """Test strategy name format includes parameters."""
        s = PMLateSnipeStrategy(threshold=Decimal("0.85"), window_seconds=45)
        assert s.name == "pm_late_snipe_0.85_45s"

    def test_invalid_threshold_low(self) -> None:
        """Test that threshold <= 0.5 raises ValueError."""
        with pytest.raises(ValueError, match="threshold must be in"):
            PMLateSnipeStrategy(threshold=Decimal("0.5"))

    def test_invalid_threshold_high(self) -> None:
        """Test that threshold >= 1.0 raises ValueError."""
        with pytest.raises(ValueError, match="threshold must be in"):
            PMLateSnipeStrategy(threshold=Decimal("1.0"))

    def test_invalid_window_seconds(self) -> None:
        """Test that window_seconds < 1 raises ValueError."""
        with pytest.raises(ValueError, match="window_seconds must be >= 1"):
            PMLateSnipeStrategy(window_seconds=0)

    def test_no_signal_outside_window(self) -> None:
        """Test no signal when more than window_seconds remain."""
        s = PMLateSnipeStrategy(threshold=_THRESHOLD, window_seconds=_WINDOW)
        # 120 seconds before end - outside the 60s window
        ts = _END_EPOCH - 120
        snap = _snap(ts, "0.95", "0.05")
        assert s.on_snapshot(snap, []) is None

    def test_buy_signal_yes_above_threshold(self) -> None:
        """Test BUY signal when YES price >= threshold in last window."""
        s = PMLateSnipeStrategy(threshold=_THRESHOLD, window_seconds=_WINDOW)
        # 30 seconds before end - inside window
        ts = _END_EPOCH - 30
        snap = _snap(ts, "0.92", "0.08")
        signal = s.on_snapshot(snap, [])
        assert signal is not None
        assert signal.side == Side.BUY
        assert "Late snipe YES" in signal.reason
        assert "30s remaining" in signal.reason

    def test_sell_signal_no_above_threshold(self) -> None:
        """Test SELL signal when NO price >= threshold in last window."""
        s = PMLateSnipeStrategy(threshold=_THRESHOLD, window_seconds=_WINDOW)
        ts = _END_EPOCH - 45
        snap = _snap(ts, "0.08", "0.92")
        signal = s.on_snapshot(snap, [])
        assert signal is not None
        assert signal.side == Side.SELL
        assert "Late snipe NO" in signal.reason

    def test_no_signal_when_below_threshold(self) -> None:
        """Test no signal when both sides below threshold in window."""
        s = PMLateSnipeStrategy(threshold=_THRESHOLD, window_seconds=_WINDOW)
        ts = _END_EPOCH - 30
        snap = _snap(ts, "0.60", "0.40")
        assert s.on_snapshot(snap, []) is None

    def test_only_signals_once_per_market(self) -> None:
        """Test that only one signal is generated per condition_id."""
        s = PMLateSnipeStrategy(threshold=_THRESHOLD, window_seconds=_WINDOW)
        ts = _END_EPOCH - 30
        snap1 = _snap(ts, "0.92", "0.08")
        snap2 = _snap(ts + 5, "0.95", "0.05")
        assert s.on_snapshot(snap1, []) is not None
        assert s.on_snapshot(snap2, [snap1]) is None

    def test_signal_at_exact_threshold(self) -> None:
        """Test BUY signal when YES price exactly equals threshold."""
        s = PMLateSnipeStrategy(threshold=_THRESHOLD, window_seconds=_WINDOW)
        ts = _END_EPOCH - 10
        snap = _snap(ts, "0.90", "0.10")
        signal = s.on_snapshot(snap, [])
        assert signal is not None
        assert signal.side == Side.BUY

    def test_signal_at_window_boundary(self) -> None:
        """Test signal at exactly the window boundary (60s remaining)."""
        s = PMLateSnipeStrategy(threshold=_THRESHOLD, window_seconds=_WINDOW)
        ts = _END_EPOCH - _WINDOW
        snap = _snap(ts, "0.95", "0.05")
        signal = s.on_snapshot(snap, [])
        assert signal is not None

    def test_no_signal_with_empty_end_date(self) -> None:
        """Test no signal when end_date is empty."""
        s = PMLateSnipeStrategy(threshold=_THRESHOLD, window_seconds=_WINDOW)
        snap = _snap(_END_EPOCH - 10, "0.95", "0.05", end_date="")
        assert s.on_snapshot(snap, []) is None

    def test_no_signal_with_invalid_end_date(self) -> None:
        """Test no signal when end_date cannot be parsed."""
        s = PMLateSnipeStrategy(threshold=_THRESHOLD, window_seconds=_WINDOW)
        snap = _snap(_END_EPOCH - 10, "0.95", "0.05", end_date="not-a-date")
        assert s.on_snapshot(snap, []) is None

    def test_signal_after_market_end(self) -> None:
        """Test signal fires even if timestamp is past end_date (0s remaining)."""
        s = PMLateSnipeStrategy(threshold=_THRESHOLD, window_seconds=_WINDOW)
        ts = _END_EPOCH + 5  # 5 seconds past end
        snap = _snap(ts, "0.99", "0.01")
        signal = s.on_snapshot(snap, [])
        assert signal is not None

    def test_configurable_window(self) -> None:
        """Test that a custom window_seconds is respected."""
        s = PMLateSnipeStrategy(threshold=_THRESHOLD, window_seconds=30)
        # 45 seconds remaining - outside 30s window
        snap1 = _snap(_END_EPOCH - 45, "0.95", "0.05")
        assert s.on_snapshot(snap1, []) is None
        # 25 seconds remaining - inside 30s window
        snap2 = _snap(_END_EPOCH - 25, "0.95", "0.05")
        assert s.on_snapshot(snap2, []) is not None
