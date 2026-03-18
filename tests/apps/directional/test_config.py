"""Tests for DirectionalConfig YAML loading and override merging."""

from decimal import Decimal
from pathlib import Path

import pytest

from trading_tools.apps.directional.config import DirectionalConfig

_DEFAULT_CAPITAL = Decimal(100)
_CUSTOM_CAPITAL = Decimal(500)
_DEFAULT_POLL_INTERVAL = 3
_CUSTOM_POLL_INTERVAL = 5
_OVERRIDE_POLL_INTERVAL = 10
_DEFAULT_MAX_OPEN = 10
_DEFAULT_KELLY = Decimal("0.5")
_DEFAULT_MIN_EDGE = Decimal("0.05")


class TestDefaults:
    """Verify default values are sensible."""

    def test_default_poll_interval(self) -> None:
        """Default poll interval is 3 seconds."""
        config = DirectionalConfig()
        assert config.poll_interval == _DEFAULT_POLL_INTERVAL

    def test_default_capital(self) -> None:
        """Default capital is 100 USDC."""
        config = DirectionalConfig()
        assert config.capital == _DEFAULT_CAPITAL

    def test_default_kelly_fraction(self) -> None:
        """Default kelly fraction is 0.5 (half-Kelly)."""
        config = DirectionalConfig()
        assert config.kelly_fraction == _DEFAULT_KELLY

    def test_default_min_edge(self) -> None:
        """Default min edge is 0.05."""
        config = DirectionalConfig()
        assert config.min_edge == _DEFAULT_MIN_EDGE

    def test_default_entry_window(self) -> None:
        """Default entry window is 30s start, 10s end."""
        config = DirectionalConfig()
        assert config.entry_window_start == 30
        assert config.entry_window_end == 10

    def test_default_series_slugs(self) -> None:
        """Default series slugs cover BTC and ETH 5-minute markets."""
        config = DirectionalConfig()
        assert config.series_slugs == ("btc-updown-5m", "eth-updown-5m")

    def test_default_max_open_positions(self) -> None:
        """Default max open positions is 10."""
        config = DirectionalConfig()
        assert config.max_open_positions == _DEFAULT_MAX_OPEN

    def test_default_estimator_weights_sum_to_one(self) -> None:
        """Estimator weights sum to 1.0 for interpretability."""
        config = DirectionalConfig()
        total = (
            config.w_momentum
            + config.w_volatility
            + config.w_volume
            + config.w_book_imbalance
            + config.w_rsi
            + config.w_price_change
            + config.w_whale
        )
        assert total == Decimal(1)


class TestFromYaml:
    """Test loading config from YAML files."""

    def test_load_basic_yaml(self, tmp_path: Path) -> None:
        """Load config with overridden capital and poll_interval."""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text("capital: 500\npoll_interval: 5\nmin_edge: 0.10\n")
        config = DirectionalConfig.from_yaml(yaml_file)
        assert config.capital == _CUSTOM_CAPITAL
        assert config.poll_interval == _CUSTOM_POLL_INTERVAL
        assert config.min_edge == Decimal("0.10")

    def test_unknown_keys_ignored(self, tmp_path: Path) -> None:
        """Unknown YAML keys are silently dropped."""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text("capital: 200\nunknown_key: 42\n")
        config = DirectionalConfig.from_yaml(yaml_file)
        assert config.capital == Decimal(200)

    def test_empty_yaml(self, tmp_path: Path) -> None:
        """Empty YAML file produces all-default config."""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text("")
        config = DirectionalConfig.from_yaml(yaml_file)
        assert config.capital == _DEFAULT_CAPITAL

    def test_invalid_decimal_raises(self, tmp_path: Path) -> None:
        """Non-numeric Decimal field raises ValueError."""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text("capital: not_a_number\n")
        with pytest.raises(ValueError, match="Cannot convert"):
            DirectionalConfig.from_yaml(yaml_file)

    def test_load_estimator_weights(self, tmp_path: Path) -> None:
        """Estimator weights can be overridden via YAML."""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text("w_momentum: 0.50\nw_rsi: 0.20\n")
        config = DirectionalConfig.from_yaml(yaml_file)
        assert config.w_momentum == Decimal("0.50")
        assert config.w_rsi == Decimal("0.20")


class TestWithOverrides:
    """Test the layered override pattern."""

    def test_override_capital(self) -> None:
        """CLI override replaces base config value."""
        base = DirectionalConfig()
        result = DirectionalConfig.with_overrides(base, capital=_CUSTOM_CAPITAL)
        assert result.capital == _CUSTOM_CAPITAL

    def test_none_overrides_skipped(self) -> None:
        """None override values leave the base unchanged."""
        base = DirectionalConfig(capital=_CUSTOM_CAPITAL)
        result = DirectionalConfig.with_overrides(base, capital=None)
        assert result.capital == _CUSTOM_CAPITAL

    def test_unknown_overrides_ignored(self) -> None:
        """Unknown override keys are silently dropped."""
        base = DirectionalConfig()
        result = DirectionalConfig.with_overrides(base, bogus_field="hello")
        assert result.capital == _DEFAULT_CAPITAL

    def test_returns_new_instance(self) -> None:
        """Overrides return a new config, not a mutation of the base."""
        base = DirectionalConfig()
        result = DirectionalConfig.with_overrides(base, poll_interval=_OVERRIDE_POLL_INTERVAL)
        assert result is not base
        assert result.poll_interval == _OVERRIDE_POLL_INTERVAL
        assert base.poll_interval == _DEFAULT_POLL_INTERVAL

    def test_frozen_immutability(self) -> None:
        """Config dataclass is frozen — attribute assignment raises."""
        config = DirectionalConfig()
        with pytest.raises(AttributeError):
            config.capital = Decimal(999)  # type: ignore[misc]
