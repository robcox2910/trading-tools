"""Tests for prediction market mean reversion strategy."""

from decimal import Decimal

import pytest

from trading_tools.apps.polymarket_bot.models import MarketSnapshot
from trading_tools.apps.polymarket_bot.protocols import PredictionMarketStrategy
from trading_tools.apps.polymarket_bot.strategies.mean_reversion import (
    PMMeanReversionStrategy,
)
from trading_tools.clients.polymarket.models import OrderBook
from trading_tools.core.models import ZERO, Side, Signal

_CONDITION_ID = "cond_test"


def _empty_book() -> OrderBook:
    """Create an empty order book for testing."""
    return OrderBook(token_id="tok1", bids=(), asks=(), spread=ZERO, midpoint=ZERO)


def _snap(ts: int, yes_price: str) -> MarketSnapshot:
    """Create a snapshot with the given timestamp and YES price.

    Args:
        ts: Unix epoch seconds.
        yes_price: YES token price as a string.

    Returns:
        MarketSnapshot with the given price.

    """
    yp = Decimal(yes_price)
    return MarketSnapshot(
        condition_id=_CONDITION_ID,
        question="Test?",
        timestamp=ts,
        yes_price=yp,
        no_price=Decimal(1) - yp,
        order_book=_empty_book(),
        volume=Decimal(1000),
        liquidity=Decimal(500),
        end_date="2026-12-31",
    )


class TestPMMeanReversionStrategy:
    """Tests for PMMeanReversionStrategy."""

    def test_satisfies_protocol(self) -> None:
        """Test that the strategy satisfies PredictionMarketStrategy protocol."""
        assert isinstance(PMMeanReversionStrategy(), PredictionMarketStrategy)

    def test_name_format(self) -> None:
        """Test strategy name format includes parameters."""
        s = PMMeanReversionStrategy(period=20, z_threshold=Decimal("1.5"))
        assert s.name == "pm_mean_reversion_20_1.5"

    def test_invalid_period_raises(self) -> None:
        """Test that period < 2 raises ValueError."""
        with pytest.raises(ValueError, match="period must be >= 2"):
            PMMeanReversionStrategy(period=1)

    def test_invalid_z_threshold_raises(self) -> None:
        """Test that z_threshold <= 0 raises ValueError."""
        with pytest.raises(ValueError, match="z_threshold must be > 0"):
            PMMeanReversionStrategy(z_threshold=ZERO)

    def test_no_signal_during_warmup(self) -> None:
        """Test no signal during warmup period."""
        s = PMMeanReversionStrategy(period=3, z_threshold=Decimal("1.5"))
        history: list[MarketSnapshot] = []
        for i in range(3):
            snap = _snap(i, "0.50")
            assert s.on_snapshot(snap, history) is None
            history.append(snap)

    def test_buy_signal_on_drop(self) -> None:
        """Test BUY signal when z-score drops below -threshold."""
        s = PMMeanReversionStrategy(period=5, z_threshold=Decimal("1.5"))
        prices = ["0.50", "0.50", "0.50", "0.50", "0.50", "0.50", "0.35"]
        history: list[MarketSnapshot] = []
        signals: list[Signal] = []
        for i, p in enumerate(prices):
            snap = _snap(i, p)
            sig = s.on_snapshot(snap, history)
            if sig is not None:
                signals.append(sig)
            history.append(snap)
        buy_signals = [sig for sig in signals if sig.side == Side.BUY]
        assert len(buy_signals) > 0
        assert "Z-score" in buy_signals[0].reason

    def test_sell_signal_on_spike(self) -> None:
        """Test SELL signal when z-score rises above +threshold."""
        s = PMMeanReversionStrategy(period=5, z_threshold=Decimal("1.5"))
        prices = ["0.50", "0.50", "0.50", "0.50", "0.50", "0.50", "0.65"]
        history: list[MarketSnapshot] = []
        signals: list[Signal] = []
        for i, p in enumerate(prices):
            snap = _snap(i, p)
            sig = s.on_snapshot(snap, history)
            if sig is not None:
                signals.append(sig)
            history.append(snap)
        sell_signals = [sig for sig in signals if sig.side == Side.SELL]
        assert len(sell_signals) > 0

    def test_no_signal_when_flat(self) -> None:
        """Test no signal when prices are constant."""
        s = PMMeanReversionStrategy(period=3, z_threshold=Decimal("1.5"))
        history: list[MarketSnapshot] = []
        for i in range(10):
            snap = _snap(i, "0.50")
            assert s.on_snapshot(snap, history) is None
            history.append(snap)
