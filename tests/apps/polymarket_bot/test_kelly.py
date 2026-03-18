"""Tests for Kelly criterion position sizer."""

from decimal import Decimal

import pytest

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


_NEAR_ZERO_PRICE = Decimal("0.01")
_NEAR_ONE_PRICE = Decimal("0.99")
_SMALL_EDGE_ABOVE_FLOOR = Decimal("0.02")
_MODERATE_ESTIMATED_PROB = Decimal("0.50")
_HIGH_ESTIMATED_PROB = Decimal("0.995")
_QUARTER_KELLY = Decimal("0.25")


class TestKellyFractionExtremePrices:
    """Parametrized edge-case tests for kelly_fraction at extreme market prices."""

    @pytest.mark.parametrize(
        ("estimated_prob", "market_price", "description"),
        [
            (_SMALL_EDGE_ABOVE_FLOOR, _NEAR_ZERO_PRICE, "tiny-edge-at-floor"),
            (_MODERATE_ESTIMATED_PROB, _NEAR_ZERO_PRICE, "large-edge-at-floor"),
            (_HIGH_ESTIMATED_PROB, _NEAR_ZERO_PRICE, "near-certain-at-floor"),
        ],
        ids=["tiny-edge-at-floor", "large-edge-at-floor", "near-certain-at-floor"],
    )
    def test_near_zero_market_price(
        self,
        estimated_prob: Decimal,
        market_price: Decimal,
        description: str,  # noqa: ARG002
    ) -> None:
        """Return a positive fraction when market price is near zero with positive edge."""
        result = kelly_fraction(estimated_prob, market_price)
        assert result > ZERO
        assert result <= _QUARTER_KELLY

    @pytest.mark.parametrize(
        ("estimated_prob", "market_price", "description"),
        [
            (_NEAR_ONE_PRICE, _NEAR_ONE_PRICE, "no-edge-at-ceiling"),
            (ONE, _NEAR_ONE_PRICE, "certain-at-ceiling"),
        ],
        ids=["no-edge-at-ceiling", "certain-at-ceiling"],
    )
    def test_near_one_market_price(
        self,
        estimated_prob: Decimal,
        market_price: Decimal,
        description: str,  # noqa: ARG002
    ) -> None:
        """Handle market prices near 1.0 without division errors."""
        result = kelly_fraction(estimated_prob, market_price)
        assert result >= ZERO

    def test_near_one_market_price_caps_at_fractional(self) -> None:
        """Verify that kelly fraction is capped at fractional when edge is extreme."""
        # estimated 1.0, market 0.01 → full kelly ≈ 1.0 → quarter = capped at 0.25
        result = kelly_fraction(ONE, _NEAR_ZERO_PRICE)
        assert result == _QUARTER_KELLY

    def test_market_price_at_zero_returns_positive(self) -> None:
        """Return positive fraction when market price is exactly zero (free bet)."""
        # edge = 0.5, denominator = 1.0 → full kelly = 0.5, quarter = 0.125
        result = kelly_fraction(_MODERATE_ESTIMATED_PROB, ZERO)
        expected = Decimal("0.125")
        assert result == expected

    def test_estimated_below_near_zero_market_returns_zero(self) -> None:
        """Return zero when estimated probability is below a near-zero market price."""
        result = kelly_fraction(ZERO, _NEAR_ZERO_PRICE)
        assert result == ZERO
