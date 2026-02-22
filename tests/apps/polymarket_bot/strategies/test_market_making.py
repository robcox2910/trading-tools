"""Tests for prediction market market making strategy."""

from decimal import Decimal

import pytest

from trading_tools.apps.polymarket_bot.models import MarketSnapshot
from trading_tools.apps.polymarket_bot.protocols import PredictionMarketStrategy
from trading_tools.apps.polymarket_bot.strategies.market_making import (
    PMMarketMakingStrategy,
)
from trading_tools.clients.polymarket.models import OrderBook
from trading_tools.core.models import ZERO, Side

_CONDITION_ID = "cond_mm"


def _empty_book() -> OrderBook:
    """Create an empty order book for testing."""
    return OrderBook(token_id="tok1", bids=(), asks=(), spread=ZERO, midpoint=ZERO)


def _snap(ts: int, yes_price: str, no_price: str | None = None) -> MarketSnapshot:
    """Create a snapshot with the given YES price.

    Args:
        ts: Unix epoch seconds.
        yes_price: YES token price as a string.
        no_price: NO token price as a string (defaults to 1 - yes_price).

    Returns:
        MarketSnapshot with the given prices.

    """
    yp = Decimal(yes_price)
    np_ = Decimal(no_price) if no_price else Decimal(1) - yp
    return MarketSnapshot(
        condition_id=_CONDITION_ID,
        question="Test?",
        timestamp=ts,
        yes_price=yp,
        no_price=np_,
        order_book=_empty_book(),
        volume=Decimal(1000),
        liquidity=Decimal(500),
        end_date="2026-12-31",
    )


class TestPMMarketMakingStrategy:
    """Tests for PMMarketMakingStrategy."""

    def test_satisfies_protocol(self) -> None:
        """Test that the strategy satisfies PredictionMarketStrategy protocol."""
        assert isinstance(PMMarketMakingStrategy(), PredictionMarketStrategy)

    def test_name_format(self) -> None:
        """Test strategy name format includes parameters."""
        s = PMMarketMakingStrategy(spread_pct=Decimal("0.03"), max_inventory=5)
        assert s.name == "pm_market_making_0.03_5"

    def test_invalid_spread_raises(self) -> None:
        """Test that spread_pct <= 0 raises ValueError."""
        with pytest.raises(ValueError, match="spread_pct must be > 0"):
            PMMarketMakingStrategy(spread_pct=ZERO)

    def test_invalid_max_inventory_raises(self) -> None:
        """Test that max_inventory < 1 raises ValueError."""
        with pytest.raises(ValueError, match="max_inventory must be >= 1"):
            PMMarketMakingStrategy(max_inventory=0)

    def test_no_signal_on_first_snapshot(self) -> None:
        """Test no signal on the very first snapshot (no previous price)."""
        s = PMMarketMakingStrategy()
        snap = _snap(0, "0.50")
        assert s.on_snapshot(snap, []) is None

    def test_buy_signal_on_price_drop_below_bid(self) -> None:
        """Test BUY signal when price crosses below virtual bid."""
        s = PMMarketMakingStrategy(spread_pct=Decimal("0.10"), max_inventory=5)
        # midpoint = 0.50, half_spread = 0.05, bid = 0.45
        snap1 = _snap(0, "0.50")
        s.on_snapshot(snap1, [])

        snap2 = _snap(1, "0.40")
        sig = s.on_snapshot(snap2, [snap1])
        assert sig is not None
        assert sig.side == Side.BUY
        assert "virtual bid" in sig.reason

    def test_sell_signal_on_price_rise_above_ask(self) -> None:
        """Test SELL signal when price crosses above virtual ask."""
        s = PMMarketMakingStrategy(spread_pct=Decimal("0.10"), max_inventory=5)
        # midpoint = 0.50, half_spread = 0.05, ask = 0.55
        snap1 = _snap(0, "0.50")
        s.on_snapshot(snap1, [])

        snap2 = _snap(1, "0.60")
        sig = s.on_snapshot(snap2, [snap1])
        assert sig is not None
        assert sig.side == Side.SELL
        assert "virtual ask" in sig.reason

    def test_inventory_limit_stops_buying(self) -> None:
        """Test that inventory limit prevents additional buys."""
        s = PMMarketMakingStrategy(spread_pct=Decimal("0.10"), max_inventory=1)
        # First buy fills the inventory
        s.on_snapshot(_snap(0, "0.50"), [])
        sig1 = s.on_snapshot(_snap(1, "0.40"), [])
        assert sig1 is not None
        assert sig1.side == Side.BUY

        # Second attempt: price drops again but inventory is full
        s.on_snapshot(_snap(2, "0.50"), [])
        sig2 = s.on_snapshot(_snap(3, "0.40"), [])
        assert sig2 is None

    def test_no_signal_within_spread(self) -> None:
        """Test no signal when price stays within the spread."""
        s = PMMarketMakingStrategy(spread_pct=Decimal("0.10"), max_inventory=5)
        s.on_snapshot(_snap(0, "0.50"), [])
        # Price moves slightly but stays within spread
        sig = s.on_snapshot(_snap(1, "0.49"), [])
        assert sig is None
