"""Tests for Kelly criterion position sizer."""

from decimal import Decimal

from trading_tools.apps.polymarket_bot.kelly import kelly_fraction
from trading_tools.core.models import ONE, ZERO


class TestKellyFraction:
    """Tests for the kelly_fraction pure function."""

    def test_positive_edge_returns_fraction(self) -> None:
        """Test that a positive edge produces a non-zero fraction."""
        # estimated 0.7, market 0.5 → full kelly = (0.7-0.5)/(1-0.5) = 0.4
        # quarter kelly = 0.4 * 0.25 = 0.1
        result = kelly_fraction(Decimal("0.7"), Decimal("0.5"))
        assert result == Decimal("0.1")

    def test_no_edge_returns_zero(self) -> None:
        """Test that no edge (estimated == market) returns zero."""
        result = kelly_fraction(Decimal("0.5"), Decimal("0.5"))
        assert result == ZERO

    def test_negative_edge_returns_zero(self) -> None:
        """Test that a negative edge (estimated < market) returns zero."""
        result = kelly_fraction(Decimal("0.3"), Decimal("0.5"))
        assert result == ZERO

    def test_full_kelly(self) -> None:
        """Test full Kelly (fractional=1.0)."""
        # estimated 0.8, market 0.5 → full kelly = 0.3/0.5 = 0.6
        result = kelly_fraction(Decimal("0.8"), Decimal("0.5"), fractional=ONE)
        assert result == Decimal("0.6")

    def test_half_kelly(self) -> None:
        """Test half Kelly (fractional=0.5)."""
        # full kelly = 0.4, half = 0.2
        result = kelly_fraction(Decimal("0.7"), Decimal("0.5"), fractional=Decimal("0.5"))
        assert result == Decimal("0.2")

    def test_market_price_at_one_returns_zero(self) -> None:
        """Test that market price of 1.0 returns zero (division guard)."""
        result = kelly_fraction(Decimal("1.0"), ONE)
        assert result == ZERO

    def test_market_price_above_one_returns_zero(self) -> None:
        """Test that market price above 1.0 returns zero."""
        result = kelly_fraction(Decimal("0.9"), Decimal("1.1"))
        assert result == ZERO

    def test_small_edge(self) -> None:
        """Test a very small but positive edge."""
        # estimated 0.51, market 0.50 → full kelly = 0.01/0.50 = 0.02
        # quarter kelly = 0.005
        result = kelly_fraction(Decimal("0.51"), Decimal("0.50"))
        assert result == Decimal("0.005")

    def test_large_edge(self) -> None:
        """Test a large edge (near certainty)."""
        # estimated 0.95, market 0.10 → full kelly = 0.85/0.90 ≈ 0.9444
        # quarter kelly ≈ 0.2361
        result = kelly_fraction(Decimal("0.95"), Decimal("0.10"))
        expected = (Decimal("0.85") / Decimal("0.90")) * Decimal("0.25")
        assert result == expected
