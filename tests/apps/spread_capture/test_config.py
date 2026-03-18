"""Tests for SpreadCaptureConfig YAML loading and override merging."""

from decimal import Decimal
from pathlib import Path

import pytest

from trading_tools.apps.spread_capture.config import SpreadCaptureConfig

_DEFAULT_CAPITAL = Decimal(100)
_CUSTOM_CAPITAL = Decimal(500)
_CUSTOM_COMBINED = Decimal("0.96")
_DEFAULT_POLL_INTERVAL = 5
_CUSTOM_POLL_INTERVAL = 10
_OVERRIDE_POLL_INTERVAL = 20
_DEFAULT_MAX_OPEN = 10


class TestDefaults:
    """Verify default values are sensible."""

    def test_default_poll_interval(self) -> None:
        """Default poll interval is 5 seconds."""
        config = SpreadCaptureConfig()
        assert config.poll_interval == _DEFAULT_POLL_INTERVAL

    def test_default_max_combined_cost(self) -> None:
        """Default max combined cost is 0.97."""
        config = SpreadCaptureConfig()
        assert config.max_combined_cost == Decimal("0.97")

    def test_default_min_spread_margin(self) -> None:
        """Default min spread margin is 0.01."""
        config = SpreadCaptureConfig()
        assert config.min_spread_margin == Decimal("0.01")

    def test_default_series_slugs(self) -> None:
        """Default series slugs cover BTC and ETH 5-minute markets."""
        config = SpreadCaptureConfig()
        assert config.series_slugs == ("btc-updown-5m", "eth-updown-5m")

    def test_default_max_open_positions(self) -> None:
        """Default max open positions is 10."""
        config = SpreadCaptureConfig()
        assert config.max_open_positions == _DEFAULT_MAX_OPEN


class TestFromYaml:
    """Test loading config from YAML files."""

    def test_load_basic_yaml(self, tmp_path: Path) -> None:
        """Load config with overridden capital and max_combined_cost."""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text("capital: 500\nmax_combined_cost: 0.96\npoll_interval: 10\n")
        config = SpreadCaptureConfig.from_yaml(yaml_file)
        assert config.capital == _CUSTOM_CAPITAL
        assert config.max_combined_cost == _CUSTOM_COMBINED
        assert config.poll_interval == _CUSTOM_POLL_INTERVAL

    def test_unknown_keys_ignored(self, tmp_path: Path) -> None:
        """Unknown YAML keys are silently dropped."""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text("capital: 200\nunknown_key: 42\n")
        config = SpreadCaptureConfig.from_yaml(yaml_file)
        assert config.capital == Decimal(200)

    def test_empty_yaml(self, tmp_path: Path) -> None:
        """Empty YAML file produces all-default config."""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text("")
        config = SpreadCaptureConfig.from_yaml(yaml_file)
        assert config.capital == _DEFAULT_CAPITAL

    def test_invalid_decimal_raises(self, tmp_path: Path) -> None:
        """Non-numeric Decimal field raises ValueError."""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text("capital: not_a_number\n")
        with pytest.raises(ValueError, match="Cannot convert"):
            SpreadCaptureConfig.from_yaml(yaml_file)


class TestWithOverrides:
    """Test the layered override pattern."""

    def test_override_capital(self) -> None:
        """CLI override replaces base config value."""
        base = SpreadCaptureConfig()
        result = SpreadCaptureConfig.with_overrides(base, capital=_CUSTOM_CAPITAL)
        assert result.capital == _CUSTOM_CAPITAL

    def test_none_overrides_skipped(self) -> None:
        """None override values leave the base unchanged."""
        base = SpreadCaptureConfig(capital=_CUSTOM_CAPITAL)
        result = SpreadCaptureConfig.with_overrides(base, capital=None)
        assert result.capital == _CUSTOM_CAPITAL

    def test_unknown_overrides_ignored(self) -> None:
        """Unknown override keys are silently dropped."""
        base = SpreadCaptureConfig()
        result = SpreadCaptureConfig.with_overrides(base, bogus_field="hello")
        assert result.capital == _DEFAULT_CAPITAL

    def test_returns_new_instance(self) -> None:
        """Overrides return a new config, not a mutation of the base."""
        base = SpreadCaptureConfig()
        result = SpreadCaptureConfig.with_overrides(base, poll_interval=20)
        assert result is not base
        assert result.poll_interval == _OVERRIDE_POLL_INTERVAL
        assert base.poll_interval == _DEFAULT_POLL_INTERVAL

    def test_frozen_immutability(self) -> None:
        """Config dataclass is frozen — attribute assignment raises."""
        config = SpreadCaptureConfig()
        with pytest.raises(AttributeError):
            config.capital = Decimal(999)  # type: ignore[misc]
