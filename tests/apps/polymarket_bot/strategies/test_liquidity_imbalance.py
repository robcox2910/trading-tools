"""Tests for prediction market liquidity imbalance strategy."""

from decimal import Decimal

import pytest

from trading_tools.apps.polymarket_bot.models import MarketSnapshot
from trading_tools.apps.polymarket_bot.protocols import PredictionMarketStrategy
from trading_tools.apps.polymarket_bot.strategies.liquidity_imbalance import (
    PMLiquidityImbalanceStrategy,
)
from trading_tools.clients.polymarket.models import OrderBook, OrderLevel
from trading_tools.core.models import Side

_CONDITION_ID = "cond_li"


def _book(bid_sizes: list[str], ask_sizes: list[str]) -> OrderBook:
    """Create an order book with given bid and ask sizes.

    Args:
        bid_sizes: List of bid sizes as strings.
        ask_sizes: List of ask sizes as strings.

    Returns:
        OrderBook with the specified levels.

    """
    bids = tuple(OrderLevel(price=Decimal("0.50"), size=Decimal(s)) for s in bid_sizes)
    asks = tuple(OrderLevel(price=Decimal("0.55"), size=Decimal(s)) for s in ask_sizes)
    return OrderBook(
        token_id="tok1",
        bids=bids,
        asks=asks,
        spread=Decimal("0.05"),
        midpoint=Decimal("0.525"),
    )


def _snap(book: OrderBook) -> MarketSnapshot:
    """Create a snapshot with the given order book.

    Args:
        book: OrderBook instance.

    Returns:
        MarketSnapshot with the given book.

    """
    return MarketSnapshot(
        condition_id=_CONDITION_ID,
        question="Test?",
        timestamp=1000,
        yes_price=Decimal("0.50"),
        no_price=Decimal("0.50"),
        order_book=book,
        volume=Decimal(1000),
        liquidity=Decimal(500),
        end_date="2026-12-31",
    )


class TestPMLiquidityImbalanceStrategy:
    """Tests for PMLiquidityImbalanceStrategy."""

    def test_satisfies_protocol(self) -> None:
        """Test that the strategy satisfies PredictionMarketStrategy protocol."""
        assert isinstance(PMLiquidityImbalanceStrategy(), PredictionMarketStrategy)

    def test_name_format(self) -> None:
        """Test strategy name format includes parameters."""
        s = PMLiquidityImbalanceStrategy(imbalance_threshold=Decimal("0.65"), depth_levels=5)
        assert s.name == "pm_liquidity_imbalance_0.65_5"

    def test_invalid_threshold_too_low(self) -> None:
        """Test that threshold <= 0.5 raises ValueError."""
        with pytest.raises(ValueError, match="imbalance_threshold must be in"):
            PMLiquidityImbalanceStrategy(imbalance_threshold=Decimal("0.5"))

    def test_invalid_threshold_too_high(self) -> None:
        """Test that threshold >= 1.0 raises ValueError."""
        with pytest.raises(ValueError, match="imbalance_threshold must be in"):
            PMLiquidityImbalanceStrategy(imbalance_threshold=Decimal("1.0"))

    def test_invalid_depth_raises(self) -> None:
        """Test that depth_levels < 1 raises ValueError."""
        with pytest.raises(ValueError, match="depth_levels must be >= 1"):
            PMLiquidityImbalanceStrategy(depth_levels=0)

    def test_buy_signal_heavy_bid_pressure(self) -> None:
        """Test BUY signal when bids significantly outweigh asks."""
        s = PMLiquidityImbalanceStrategy(imbalance_threshold=Decimal("0.65"), depth_levels=5)
        # bids: 800, asks: 200 → imbalance = 0.80
        book = _book(["500", "300"], ["100", "100"])
        snap = _snap(book)
        sig = s.on_snapshot(snap, [])
        assert sig is not None
        assert sig.side == Side.BUY
        assert "imbalance" in sig.reason.lower()

    def test_sell_signal_heavy_ask_pressure(self) -> None:
        """Test SELL signal when asks significantly outweigh bids."""
        s = PMLiquidityImbalanceStrategy(imbalance_threshold=Decimal("0.65"), depth_levels=5)
        # bids: 200, asks: 800 → imbalance = 0.20 < 0.35
        book = _book(["100", "100"], ["500", "300"])
        snap = _snap(book)
        sig = s.on_snapshot(snap, [])
        assert sig is not None
        assert sig.side == Side.SELL

    def test_no_signal_balanced_book(self) -> None:
        """Test no signal when order book is balanced."""
        s = PMLiquidityImbalanceStrategy(imbalance_threshold=Decimal("0.65"), depth_levels=5)
        book = _book(["500", "500"], ["500", "500"])
        snap = _snap(book)
        assert s.on_snapshot(snap, []) is None

    def test_no_signal_empty_book(self) -> None:
        """Test no signal when order book is empty."""
        s = PMLiquidityImbalanceStrategy()
        book = _book([], [])
        snap = _snap(book)
        assert s.on_snapshot(snap, []) is None

    def test_depth_levels_limits_analysis(self) -> None:
        """Test that only the specified number of levels are considered."""
        s = PMLiquidityImbalanceStrategy(imbalance_threshold=Decimal("0.65"), depth_levels=1)
        # Top level: bid=100, ask=100 → balanced
        # But deeper levels are imbalanced — should be ignored
        book = _book(["100", "900"], ["100", "10"])
        snap = _snap(book)
        assert s.on_snapshot(snap, []) is None
