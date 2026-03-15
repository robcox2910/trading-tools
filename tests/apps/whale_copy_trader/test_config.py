"""Tests for WhaleCopyConfig YAML loading and override merging.

Verify the ``from_yaml`` and ``with_overrides`` classmethods that enable
layered configuration: dataclass defaults → YAML file → CLI overrides.
"""

from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from trading_tools.apps.whale_copy_trader.config import WhaleCopyConfig

_YAML_POLL_INTERVAL = 10
_YAML_CAPITAL = 500
_OVERRIDE_POLL_INTERVAL = 30
_OVERRIDE_CAPITAL = 999
_BASE_CAPITAL_200 = 200


@pytest.fixture
def yaml_config(tmp_path: Path) -> Path:
    """Write a minimal YAML config file and return its path."""
    data = {
        "whale_address": "0xYAML",
        "capital": "500",
        "max_position_pct": "0.25",
        "poll_interval": _YAML_POLL_INTERVAL,
        "adaptive_kelly": False,
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.dump(data))
    return path


class TestFromYaml:
    """Test ``WhaleCopyConfig.from_yaml``."""

    def test_loads_valid_yaml(self, yaml_config: Path) -> None:
        """Load a YAML file and verify fields are set correctly."""
        cfg = WhaleCopyConfig.from_yaml(yaml_config)

        assert cfg.whale_address == "0xYAML"
        assert cfg.capital == Decimal(_YAML_CAPITAL)
        assert cfg.max_position_pct == Decimal("0.25")
        assert cfg.poll_interval == _YAML_POLL_INTERVAL
        assert cfg.adaptive_kelly is False

    def test_converts_numeric_strings_to_decimal(self, tmp_path: Path) -> None:
        """Ensure string values for Decimal fields are converted."""
        data = {"whale_address": "0xABC", "min_bias": "2.5", "defensive_hedge_pct": "0.30"}
        path = tmp_path / "config.yaml"
        path.write_text(yaml.dump(data))

        cfg = WhaleCopyConfig.from_yaml(path)

        assert cfg.min_bias == Decimal("2.5")
        assert cfg.defensive_hedge_pct == Decimal("0.30")

    def test_converts_bare_numeric_to_decimal(self, tmp_path: Path) -> None:
        """Ensure bare YAML numbers (float/int) are converted to Decimal."""
        data = {"whale_address": "0xABC", "capital": 1000, "kelly_fraction": 0.25}
        path = tmp_path / "config.yaml"
        path.write_text(yaml.dump(data))

        cfg = WhaleCopyConfig.from_yaml(path)

        assert cfg.capital == Decimal(1000)
        assert cfg.kelly_fraction == Decimal("0.25")

    def test_ignores_unknown_keys(self, tmp_path: Path) -> None:
        """Unknown keys in the YAML should be silently dropped."""
        data = {
            "whale_address": "0xABC",
            "unknown_future_field": "hello",
            "another_unknown": 42,
        }
        path = tmp_path / "config.yaml"
        path.write_text(yaml.dump(data))

        cfg = WhaleCopyConfig.from_yaml(path)

        assert cfg.whale_address == "0xABC"
        # Defaults preserved for unset fields
        assert cfg.capital == Decimal(100)

    def test_empty_yaml_uses_defaults(self, tmp_path: Path) -> None:
        """An empty YAML file should produce a config with all defaults."""
        path = tmp_path / "empty.yaml"
        path.write_text("")

        # whale_address has no default, so we need it in the file
        # Actually, empty YAML means no whale_address → should raise
        with pytest.raises(TypeError):
            WhaleCopyConfig.from_yaml(path)

    def test_file_not_found_raises(self, tmp_path: Path) -> None:
        """Raise FileNotFoundError for a missing YAML path."""
        with pytest.raises(FileNotFoundError):
            WhaleCopyConfig.from_yaml(tmp_path / "nonexistent.yaml")

    def test_invalid_decimal_raises(self, tmp_path: Path) -> None:
        """Raise ValueError for unconvertible Decimal fields."""
        data = {"whale_address": "0xABC", "capital": "not-a-number"}
        path = tmp_path / "config.yaml"
        path.write_text(yaml.dump(data))

        with pytest.raises(ValueError, match="Cannot convert capital"):
            WhaleCopyConfig.from_yaml(path)


class TestWithOverrides:
    """Test ``WhaleCopyConfig.with_overrides``."""

    def test_applies_non_none_values(self) -> None:
        """Non-None overrides replace base values."""
        base = WhaleCopyConfig(whale_address="0xBASE", capital=Decimal(100))

        result = WhaleCopyConfig.with_overrides(
            base, capital=Decimal(_OVERRIDE_CAPITAL), poll_interval=_OVERRIDE_POLL_INTERVAL
        )

        assert result.capital == Decimal(_OVERRIDE_CAPITAL)
        assert result.poll_interval == _OVERRIDE_POLL_INTERVAL
        assert result.whale_address == "0xBASE"

    def test_preserves_base_when_override_is_none(self) -> None:
        """None overrides do not change the base value."""
        base = WhaleCopyConfig(
            whale_address="0xBASE",
            capital=Decimal(_BASE_CAPITAL_200),
            adaptive_kelly=False,
        )

        result = WhaleCopyConfig.with_overrides(base, capital=None, adaptive_kelly=None)

        assert result.capital == Decimal(_BASE_CAPITAL_200)
        assert result.adaptive_kelly is False

    def test_ignores_unknown_override_keys(self) -> None:
        """Unknown override keys are silently dropped."""
        base = WhaleCopyConfig(whale_address="0xBASE")

        result = WhaleCopyConfig.with_overrides(base, nonexistent_field="boom")

        assert result.whale_address == "0xBASE"

    def test_returns_new_instance(self) -> None:
        """Overrides produce a new config, not a mutation of the base."""
        base = WhaleCopyConfig(whale_address="0xBASE")

        result = WhaleCopyConfig.with_overrides(base, capital=Decimal(_YAML_CAPITAL))

        assert result is not base
        assert base.capital == Decimal(100)
        assert result.capital == Decimal(_YAML_CAPITAL)


class TestLayeredPrecedence:
    """Test the full layered config: defaults → YAML → CLI overrides."""

    def test_cli_overrides_yaml(self, yaml_config: Path) -> None:
        """CLI values take precedence over YAML values."""
        cfg = WhaleCopyConfig.from_yaml(yaml_config)
        assert cfg.capital == Decimal(_YAML_CAPITAL)  # from YAML

        cfg = WhaleCopyConfig.with_overrides(cfg, capital=Decimal(_OVERRIDE_CAPITAL))
        assert cfg.capital == Decimal(_OVERRIDE_CAPITAL)  # CLI wins

    def test_yaml_overrides_defaults(self, yaml_config: Path) -> None:
        """YAML values take precedence over dataclass defaults."""
        cfg = WhaleCopyConfig.from_yaml(yaml_config)

        # YAML set capital=500 (default is 100)
        assert cfg.capital == Decimal(_YAML_CAPITAL)
        # YAML set poll_interval=10 (default is 5)
        assert cfg.poll_interval == _YAML_POLL_INTERVAL
        # YAML didn't set min_bias, so default 1.3 applies
        assert cfg.min_bias == Decimal("1.3")

    def test_full_chain(self, yaml_config: Path) -> None:
        """Exercise defaults → YAML → CLI in one flow."""
        # Step 1: YAML overrides some defaults
        cfg = WhaleCopyConfig.from_yaml(yaml_config)
        assert cfg.capital == Decimal(_YAML_CAPITAL)  # YAML
        assert cfg.min_bias == Decimal("1.3")  # default

        # Step 2: CLI overrides whale_address and min_bias, leaves capital
        cfg = WhaleCopyConfig.with_overrides(
            cfg,
            whale_address="0xCLI",
            min_bias=Decimal("2.0"),
            capital=None,  # not set on CLI → keep YAML value
        )

        assert cfg.whale_address == "0xCLI"
        assert cfg.min_bias == Decimal("2.0")
        assert cfg.capital == Decimal(_YAML_CAPITAL)  # preserved from YAML
