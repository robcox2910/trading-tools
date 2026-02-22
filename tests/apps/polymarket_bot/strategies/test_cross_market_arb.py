"""Tests for prediction market cross-market arbitrage strategy."""

from decimal import Decimal

import pytest

from trading_tools.apps.polymarket_bot.models import MarketSnapshot
from trading_tools.apps.polymarket_bot.protocols import PredictionMarketStrategy
from trading_tools.apps.polymarket_bot.strategies.cross_market_arb import (
    PMCrossMarketArbStrategy,
)
from trading_tools.clients.polymarket.models import OrderBook
from trading_tools.core.models import ZERO, Side

_CONDITION_A = "cond_a"
_CONDITION_B = "cond_b"
_CONDITION_C = "cond_c"


def _empty_book() -> OrderBook:
    """Create an empty order book for testing."""
    return OrderBook(token_id="tok1", bids=(), asks=(), spread=ZERO, midpoint=ZERO)


def _snap(condition_id: str, yes_price: str) -> MarketSnapshot:
    """Create a snapshot with the given condition_id and YES price.

    Args:
        condition_id: Market identifier.
        yes_price: YES token price as a string.

    Returns:
        MarketSnapshot with the given price.

    """
    yp = Decimal(yes_price)
    return MarketSnapshot(
        condition_id=condition_id,
        question="Test?",
        timestamp=1000,
        yes_price=yp,
        no_price=Decimal(1) - yp,
        order_book=_empty_book(),
        volume=Decimal(1000),
        liquidity=Decimal(500),
        end_date="2026-12-31",
    )


class TestPMCrossMarketArbStrategy:
    """Tests for PMCrossMarketArbStrategy."""

    def test_satisfies_protocol(self) -> None:
        """Test that the strategy satisfies PredictionMarketStrategy protocol."""
        assert isinstance(PMCrossMarketArbStrategy(), PredictionMarketStrategy)

    def test_name_format(self) -> None:
        """Test strategy name format includes parameters."""
        s = PMCrossMarketArbStrategy(min_edge=Decimal("0.02"))
        assert s.name == "pm_cross_market_arb_0.02"

    def test_invalid_min_edge_raises(self) -> None:
        """Test that min_edge <= 0 raises ValueError."""
        with pytest.raises(ValueError, match="min_edge must be > 0"):
            PMCrossMarketArbStrategy(min_edge=ZERO)

    def test_no_signal_without_related(self) -> None:
        """Test no signal when no related markets are provided."""
        s = PMCrossMarketArbStrategy()
        snap = _snap(_CONDITION_A, "0.50")
        assert s.on_snapshot(snap, []) is None
        assert s.on_snapshot(snap, [], related=None) is None

    def test_buy_signal_underpriced(self) -> None:
        """Test BUY signal when current market is underpriced relative to others."""
        s = PMCrossMarketArbStrategy(min_edge=Decimal("0.02"))
        # Three mutually exclusive outcomes: 0.20 + 0.50 + 0.50 = 1.20
        # Fair price for A: 0.20/1.20 ≈ 0.1667
        # But A is priced at 0.20 — actually overpriced
        # Let's make A underpriced: A=0.20, B=0.30, C=0.30 → sum=0.80
        # Fair for A: 0.20/0.80 = 0.25, edge = 0.25-0.20 = 0.05
        snap = _snap(_CONDITION_A, "0.20")
        related = [_snap(_CONDITION_B, "0.30"), _snap(_CONDITION_C, "0.30")]
        sig = s.on_snapshot(snap, [], related=related)
        assert sig is not None
        assert sig.side == Side.BUY
        assert "Underpriced" in sig.reason

    def test_sell_signal_overpriced(self) -> None:
        """Test SELL signal when current market is overpriced relative to others."""
        s = PMCrossMarketArbStrategy(min_edge=Decimal("0.02"))
        # A=0.60, B=0.30, C=0.30 → sum=1.20
        # Fair for A: 0.60/1.20 = 0.50, edge = 0.50-0.60 = -0.10
        snap = _snap(_CONDITION_A, "0.60")
        related = [_snap(_CONDITION_B, "0.30"), _snap(_CONDITION_C, "0.30")]
        sig = s.on_snapshot(snap, [], related=related)
        assert sig is not None
        assert sig.side == Side.SELL
        assert "Overpriced" in sig.reason

    def test_no_signal_fairly_priced(self) -> None:
        """Test no signal when markets are fairly priced (sum ≈ 1.0)."""
        s = PMCrossMarketArbStrategy(min_edge=Decimal("0.02"))
        # A=0.50, B=0.50 → sum=1.0, fair=0.50, edge=0
        snap = _snap(_CONDITION_A, "0.50")
        related = [_snap(_CONDITION_B, "0.50")]
        assert s.on_snapshot(snap, [], related=related) is None

    def test_no_signal_edge_below_min(self) -> None:
        """Test no signal when edge is below min_edge."""
        s = PMCrossMarketArbStrategy(min_edge=Decimal("0.05"))
        # A=0.49, B=0.50 → sum=0.99, fair=0.49/0.99 ≈ 0.4949, edge ≈ 0.005
        snap = _snap(_CONDITION_A, "0.49")
        related = [_snap(_CONDITION_B, "0.50")]
        assert s.on_snapshot(snap, [], related=related) is None
