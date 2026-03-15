"""Tests for the spread-capture CLI command."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from trading_tools.apps.polymarket.cli.spread_capture_cmd import _build_config

if TYPE_CHECKING:
    from pathlib import Path

_DEFAULT_COMBINED = Decimal("0.98")
_CUSTOM_COMBINED = Decimal("0.96")
_DEFAULT_CAPITAL = Decimal(100)
_CUSTOM_POLL = 10
_CUSTOM_MAX_OPEN = 5
_CRYPTO_5M_COUNT = 4


class TestBuildConfig:
    """Test CLI config builder."""

    def test_defaults_only(self) -> None:
        """Build config with no overrides uses all defaults."""
        config = _build_config(
            config_file=None,
            series_slugs_str=None,
            compound_profits=None,
            use_market_orders=None,
        )
        assert config.capital == _DEFAULT_CAPITAL
        assert config.max_combined_cost == _DEFAULT_COMBINED

    def test_series_slugs_override(self) -> None:
        """Series slugs are parsed from comma-separated string."""
        config = _build_config(
            config_file=None,
            series_slugs_str="crypto-5m",
            compound_profits=None,
            use_market_orders=None,
        )
        assert "btc-updown-5m" in config.series_slugs
        assert len(config.series_slugs) == _CRYPTO_5M_COUNT

    def test_decimal_override(self) -> None:
        """Decimal params are converted from string CLI values."""
        config = _build_config(
            config_file=None,
            series_slugs_str=None,
            compound_profits=None,
            use_market_orders=None,
            capital="500",
            max_combined_cost="0.96",
        )
        assert config.capital == Decimal(500)
        assert config.max_combined_cost == _CUSTOM_COMBINED

    def test_int_override(self) -> None:
        """Integer params pass through unchanged."""
        config = _build_config(
            config_file=None,
            series_slugs_str=None,
            compound_profits=None,
            use_market_orders=None,
            poll_interval=_CUSTOM_POLL,
            max_open_positions=_CUSTOM_MAX_OPEN,
        )
        assert config.poll_interval == _CUSTOM_POLL
        assert config.max_open_positions == _CUSTOM_MAX_OPEN

    def test_boolean_override(self) -> None:
        """Boolean params are applied when not None."""
        config = _build_config(
            config_file=None,
            series_slugs_str=None,
            compound_profits=False,
            use_market_orders=True,
        )
        assert config.compound_profits is False
        assert config.use_market_orders is True

    def test_none_overrides_skipped(self) -> None:
        """None values don't override defaults."""
        config = _build_config(
            config_file=None,
            series_slugs_str=None,
            compound_profits=None,
            use_market_orders=None,
            capital=None,
        )
        assert config.capital == _DEFAULT_CAPITAL

    def test_yaml_file_loaded(self, tmp_path: Path) -> None:
        """Config loads from YAML file when provided."""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text("capital: 999\nmax_combined_cost: 0.95\n")
        config = _build_config(
            config_file=str(yaml_file),
            series_slugs_str=None,
            compound_profits=None,
            use_market_orders=None,
        )
        assert config.capital == Decimal(999)
        assert config.max_combined_cost == Decimal("0.95")
