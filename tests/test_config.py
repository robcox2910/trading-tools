"""Tests for configuration management."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

import trading_tools.core.config as config_module
from trading_tools.core.config import ConfigError, ConfigLoader, get_config

EXPECTED_TIMEOUT = 30
EXPECTED_MAX_ATTEMPTS = 5
EXPECTED_BACKOFF = 2


class TestConfigLoader:
    """Test suite for ConfigLoader."""

    def test_load_default_config(self) -> None:
        """Test loading default configuration."""
        loader = ConfigLoader()
        assert loader.get("environment") is not None

    def test_get_with_dot_notation(self, tmp_path: Path) -> None:
        """Test getting config values with dot notation."""
        # Create a test config file
        config_file = tmp_path / "settings.yaml"
        config_file.write_text("""
revolut_x:
  api_key: test_key_123
  base_url: https://test.revolut.com
environment: test
""")

        loader = ConfigLoader(config_dir=tmp_path)
        assert loader.get("revolut_x.api_key") == "test_key_123"
        assert loader.get("revolut_x.base_url") == "https://test.revolut.com"
        assert loader.get("environment") == "test"

    def test_get_with_default(self, tmp_path: Path) -> None:
        """Test getting non-existent key returns default."""
        config_file = tmp_path / "settings.yaml"
        config_file.write_text("environment: test")

        loader = ConfigLoader(config_dir=tmp_path)
        assert loader.get("nonexistent.key", "default_value") == "default_value"

    def test_env_var_substitution(self, tmp_path: Path) -> None:
        """Test environment variable substitution."""
        config_file = tmp_path / "settings.yaml"
        config_file.write_text("""
revolut_x:
  api_key: ${TEST_API_KEY}
  base_url: ${TEST_BASE_URL:https://default.com}
""")

        with patch.dict(os.environ, {"TEST_API_KEY": "env_key_123"}):
            loader = ConfigLoader(config_dir=tmp_path)
            assert loader.get("revolut_x.api_key") == "env_key_123"
            # TEST_BASE_URL not set, should use default
            assert loader.get("revolut_x.base_url") == "https://default.com"

    def test_local_settings_override(self, tmp_path: Path) -> None:
        """Test that local settings override base settings."""
        base_config = tmp_path / "settings.yaml"
        base_config.write_text("""
revolut_x:
  api_key: base_key
  base_url: https://base.com
environment: production
""")

        local_config = tmp_path / "settings.local.yaml"
        local_config.write_text("""
revolut_x:
  api_key: local_key
environment: development
""")

        loader = ConfigLoader(config_dir=tmp_path)
        assert loader.get("revolut_x.api_key") == "local_key"
        assert loader.get("revolut_x.base_url") == "https://base.com"
        assert loader.get("environment") == "development"

    def test_get_revolut_x_config(self, tmp_path: Path) -> None:
        """Test getting Revolut X configuration."""
        config_file = tmp_path / "settings.yaml"
        config_file.write_text("""
revolut_x:
  api_key: test_key
  base_url: https://test.com
""")

        loader = ConfigLoader(config_dir=tmp_path)
        revolut_config = loader.get_revolut_x_config()
        assert revolut_config["api_key"] == "test_key"
        assert revolut_config["base_url"] == "https://test.com"

    def test_get_private_key_not_configured(self, tmp_path: Path) -> None:
        """Test getting private key when path not configured."""
        config_file = tmp_path / "settings.yaml"
        config_file.write_text("environment: test")

        loader = ConfigLoader(config_dir=tmp_path)
        with pytest.raises(ValueError, match="private_key_path is not configured"):
            loader.get_private_key()

    def test_get_private_key_file_not_found(self, tmp_path: Path) -> None:
        """Test getting private key when file doesn't exist."""
        config_file = tmp_path / "settings.yaml"
        config_file.write_text("""
revolut_x:
  private_key_path: /nonexistent/path.pem
""")

        loader = ConfigLoader(config_dir=tmp_path)
        with pytest.raises(FileNotFoundError, match="Private key not found"):
            loader.get_private_key()

    def test_get_private_key_success(self, tmp_path: Path) -> None:
        """Test successfully loading private key."""
        # Create test key file
        key_file = tmp_path / "test_key.pem"
        test_key_data = b"test private key data"
        key_file.write_bytes(test_key_data)

        # Create config pointing to key file
        config_file = tmp_path / "settings.yaml"
        config_file.write_text(f"""
revolut_x:
  private_key_path: {key_file}
""")

        loader = ConfigLoader(config_dir=tmp_path)
        key_data = loader.get_private_key()
        assert key_data == test_key_data

    def test_full_string_unresolved_env_var_raises_config_error(self, tmp_path: Path) -> None:
        """Raise ConfigError when env var is unset and has no default."""
        config_file = tmp_path / "settings.yaml"
        config_file.write_text("""
revolut_x:
  api_key: ${NONEXISTENT_TRADING_TOOLS_VAR}
""")

        with pytest.raises(ConfigError, match="Required environment variable"):
            ConfigLoader(config_dir=tmp_path)

    def test_embedded_env_var_reference_raises_config_error(self, tmp_path: Path) -> None:
        """Raise ConfigError when an env var reference is embedded in a larger string."""
        config_file = tmp_path / "settings.yaml"
        config_file.write_text("""
revolut_x:
  base_url: https://api.example.com/${NONEXISTENT_PATH_VAR}/v1
""")

        with pytest.raises(ConfigError, match="Unresolved environment variable reference"):
            ConfigLoader(config_dir=tmp_path)

    def test_env_var_with_default_does_not_raise(self, tmp_path: Path) -> None:
        """Accept env var references that include a default value."""
        config_file = tmp_path / "settings.yaml"
        config_file.write_text("""
revolut_x:
  api_key: ${NONEXISTENT_VAR_WITH_DEFAULT:fallback_value}
""")

        loader = ConfigLoader(config_dir=tmp_path)
        assert loader.get("revolut_x.api_key") == "fallback_value"

    def test_deep_merge(self, tmp_path: Path) -> None:
        """Test deep merging of nested configurations."""
        base_config = tmp_path / "settings.yaml"
        base_config.write_text("""
revolut_x:
  api_key: base_key
  timeout: 30
  retry:
    max_attempts: 3
    backoff: 2
""")

        local_config = tmp_path / "settings.local.yaml"
        local_config.write_text("""
revolut_x:
  api_key: local_key
  retry:
    max_attempts: 5
""")

        loader = ConfigLoader(config_dir=tmp_path)
        assert loader.get("revolut_x.api_key") == "local_key"
        assert loader.get("revolut_x.timeout") == EXPECTED_TIMEOUT
        assert loader.get("revolut_x.retry.max_attempts") == EXPECTED_MAX_ATTEMPTS
        assert loader.get("revolut_x.retry.backoff") == EXPECTED_BACKOFF


class TestGetConfig:
    """Test suite for the lazy singleton get_config() function."""

    def test_returns_config_loader_instance(self) -> None:
        """Return a ConfigLoader instance on first call."""
        config_module._config = None
        try:
            result = get_config()
            assert isinstance(result, ConfigLoader)
        finally:
            config_module._config = None

    def test_returns_same_instance(self) -> None:
        """Return the same ConfigLoader on subsequent calls."""
        config_module._config = None
        try:
            first = get_config()
            second = get_config()
            assert first is second
        finally:
            config_module._config = None
