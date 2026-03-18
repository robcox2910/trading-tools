"""Tests for the whale copy configuration."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from trading_tools.apps.whale_copy.config import WhaleCopyConfig, _parse_config_dict

_DEFAULT_CAPITAL = Decimal(1000)
_YAML_CAPITAL = Decimal(500)
_YAML_POLL_INTERVAL = 10
_PARSED_CAPITAL = Decimal(750)


class TestParseConfigDict:
    """Test YAML config dict parsing and type conversion."""

    def test_converts_decimal_fields(self) -> None:
        """Convert numeric values to Decimal for Decimal-typed fields."""
        data = {"capital": "750", "max_price": 0.55}
        result = _parse_config_dict(data)

        assert result["capital"] == _PARSED_CAPITAL
        assert result["max_price"] == Decimal("0.55")

    def test_passes_through_non_decimal_fields(self) -> None:
        """Pass non-Decimal fields through unchanged."""
        data = {"poll_interval": 10, "compound_profits": False}
        result = _parse_config_dict(data)

        assert result["poll_interval"] == _YAML_POLL_INTERVAL
        assert result["compound_profits"] is False

    def test_drops_unknown_keys(self) -> None:
        """Silently drop keys that do not match any dataclass field."""
        data = {"unknown_key": "value", "capital": "100"}
        result = _parse_config_dict(data)

        assert "unknown_key" not in result
        assert "capital" in result

    def test_raises_on_unconvertible_decimal(self) -> None:
        """Raise ValueError when a Decimal field has an unconvertible value."""
        data = {"capital": "not_a_number"}

        with pytest.raises(ValueError, match="Cannot convert capital"):
            _parse_config_dict(data)


class TestFromYaml:
    """Test loading WhaleCopyConfig from YAML files."""

    def test_loads_from_yaml(self, tmp_path: Path) -> None:
        """Load config from a YAML file with overrides."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump({"capital": 500, "poll_interval": 10}))

        config = WhaleCopyConfig.from_yaml(config_path)

        assert config.capital == _YAML_CAPITAL
        assert config.poll_interval == _YAML_POLL_INTERVAL

    def test_defaults_fill_missing_fields(self, tmp_path: Path) -> None:
        """Use dataclass defaults for fields not in the YAML file."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump({"capital": 500}))

        config = WhaleCopyConfig.from_yaml(config_path)

        assert config.capital == _YAML_CAPITAL
        assert config.fill_size_tokens == Decimal(5)
        assert config.max_price == Decimal("0.60")

    def test_empty_yaml_uses_all_defaults(self, tmp_path: Path) -> None:
        """Return all-default config when YAML file is empty."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text("")

        config = WhaleCopyConfig.from_yaml(config_path)

        assert config.capital == _DEFAULT_CAPITAL

    def test_file_not_found_raises(self) -> None:
        """Raise FileNotFoundError when YAML file does not exist."""
        with pytest.raises(FileNotFoundError):
            WhaleCopyConfig.from_yaml(Path("/nonexistent/config.yaml"))
