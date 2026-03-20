"""Tests for the directional grid search CLI helpers."""

from decimal import Decimal

from trading_tools.apps.polymarket.cli.directional_grid_cmd import (
    _build_grid,
    _parse_decimal_list,
    _parse_int_list,
)


class TestParseDecimalList:
    """Test comma-separated decimal parsing."""

    def test_basic(self) -> None:
        """Parse simple comma-separated decimals."""
        result = _parse_decimal_list("0.3,0.4,0.5")
        assert result == [Decimal("0.3"), Decimal("0.4"), Decimal("0.5")]

    def test_whitespace(self) -> None:
        """Handle whitespace around values."""
        result = _parse_decimal_list(" 0.1 , 0.2 ")
        assert result == [Decimal("0.1"), Decimal("0.2")]

    def test_single_value(self) -> None:
        """Single value returns a one-element list."""
        result = _parse_decimal_list("0.5")
        assert result == [Decimal("0.5")]


class TestParseIntList:
    """Test comma-separated integer parsing."""

    def test_basic(self) -> None:
        """Parse simple comma-separated integers."""
        result = _parse_int_list("60,90,120")
        assert result == [60, 90, 120]


class TestBuildGrid:
    """Test parameter grid construction."""

    def test_multi_value_params_in_grid(self) -> None:
        """Parameters with multiple values appear in the grid."""
        grid, _config = _build_grid(
            "0.30,0.50",
            "0.15",
            "0.05",
            "120",
            capital=1000.0,
            signal_lookback=1200,
        )
        assert "w_whale" in grid
        assert len(grid["w_whale"]) == 2
        assert "w_momentum" not in grid  # single value, not swept

    def test_single_value_params_set_on_config(self) -> None:
        """Single-value params are set on the base config, not in the grid."""
        grid, config = _build_grid(
            "0.50",
            "0.15",
            "0.03,0.05,0.07",
            "120",
            capital=1000.0,
            signal_lookback=1200,
        )
        assert config.w_whale == Decimal("0.50")
        assert "min_edge" in grid
        assert len(grid["min_edge"]) == 3

    def test_empty_grid_when_all_single(self) -> None:
        """Return empty grid when all params are single-value."""
        grid, _config = _build_grid(
            "0.50",
            "0.15",
            "0.05",
            "120",
            capital=1000.0,
            signal_lookback=1200,
        )
        assert not grid
