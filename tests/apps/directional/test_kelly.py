"""Tests for Kelly criterion sizing."""

from decimal import Decimal

from trading_tools.apps.directional.kelly import kelly_fraction
from trading_tools.core.models import ONE, ZERO


class TestKellyFraction:
    """Test fractional Kelly bet sizing for binary outcomes."""

    def test_positive_edge_returns_positive(self) -> None:
        """Positive edge (p > price) produces a positive fraction."""
        result = kelly_fraction(
            p_win=Decimal("0.65"),
            token_price=Decimal("0.50"),
            fractional=Decimal("0.5"),
        )
        assert result > ZERO

    def test_no_edge_returns_zero(self) -> None:
        """No edge (p == price) returns zero."""
        result = kelly_fraction(
            p_win=Decimal("0.50"),
            token_price=Decimal("0.50"),
        )
        assert result == ZERO

    def test_negative_edge_returns_zero(self) -> None:
        """Negative edge (p < price) returns zero."""
        result = kelly_fraction(
            p_win=Decimal("0.40"),
            token_price=Decimal("0.50"),
        )
        assert result == ZERO

    def test_full_kelly_formula(self) -> None:
        """Full Kelly (fractional=1) matches (p - price) / (1 - price)."""
        p = Decimal("0.70")
        price = Decimal("0.50")
        expected = (p - price) / (ONE - price)
        result = kelly_fraction(p_win=p, token_price=price, fractional=ONE, max_fraction=ONE)
        assert result == expected

    def test_half_kelly(self) -> None:
        """Half Kelly is exactly half of full Kelly."""
        p = Decimal("0.70")
        price = Decimal("0.50")
        full = kelly_fraction(p_win=p, token_price=price, fractional=ONE, max_fraction=ONE)
        half = kelly_fraction(
            p_win=p, token_price=price, fractional=Decimal("0.5"), max_fraction=ONE
        )
        assert half == full / Decimal(2)

    def test_capped_at_max_fraction(self) -> None:
        """Very high edge is capped at max_fraction."""
        result = kelly_fraction(
            p_win=Decimal("0.99"),
            token_price=Decimal("0.01"),
            fractional=ONE,
            max_fraction=Decimal("0.15"),
        )
        assert result == Decimal("0.15")

    def test_zero_probability_returns_zero(self) -> None:
        """Zero win probability returns zero."""
        result = kelly_fraction(p_win=ZERO, token_price=Decimal("0.50"))
        assert result == ZERO

    def test_one_probability_returns_zero(self) -> None:
        """Win probability of 1.0 returns zero (out of bounds)."""
        result = kelly_fraction(p_win=ONE, token_price=Decimal("0.50"))
        assert result == ZERO

    def test_zero_price_returns_zero(self) -> None:
        """Zero token price returns zero."""
        result = kelly_fraction(p_win=Decimal("0.70"), token_price=ZERO)
        assert result == ZERO

    def test_one_price_returns_zero(self) -> None:
        """Token price of 1.0 returns zero (no upside)."""
        result = kelly_fraction(p_win=Decimal("0.70"), token_price=ONE)
        assert result == ZERO

    def test_realistic_scenario(self) -> None:
        """Realistic scenario: 65% estimated, price 0.55, half-Kelly."""
        result = kelly_fraction(
            p_win=Decimal("0.65"),
            token_price=Decimal("0.55"),
            fractional=Decimal("0.5"),
            max_fraction=Decimal("0.15"),
        )
        # Full Kelly: (0.65 - 0.55) / (1 - 0.55) = 0.10 / 0.45 ≈ 0.2222
        # Half Kelly: ≈ 0.1111
        expected_full = Decimal("0.10") / Decimal("0.45")
        expected_half = expected_full * Decimal("0.5")
        assert result == expected_half

    def test_custom_max_fraction(self) -> None:
        """Custom max_fraction limits the output."""
        result = kelly_fraction(
            p_win=Decimal("0.90"),
            token_price=Decimal("0.10"),
            fractional=ONE,
            max_fraction=Decimal("0.25"),
        )
        assert result == Decimal("0.25")
