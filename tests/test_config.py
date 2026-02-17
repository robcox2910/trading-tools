"""Tests for configuration management."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from trading_tools.config import Config


class TestConfig:
    """Test suite for application configuration."""

    def test_default_base_url(self) -> None:
        """Test that default base URL is set correctly."""
        assert Config.REVOLUT_X_BASE_URL == "https://api.revolut.com/api/1.0"

    def test_default_environment(self) -> None:
        """Test that default environment is sandbox."""
        assert Config.ENVIRONMENT == "sandbox"

    @patch.dict(os.environ, {"REVOLUT_X_API_KEY": "test_api_key_12345"})
    def test_api_key_from_env(self) -> None:
        """Test loading API key from environment."""
        # Need to reload the module to pick up env changes
        from importlib import reload

        from trading_tools import config as config_module

        reload(config_module)
        assert config_module.Config.REVOLUT_X_API_KEY == "test_api_key_12345"

    def test_get_private_key_without_path_raises_error(self) -> None:
        """Test that getting private key without configured path raises error."""
        with (
            patch.object(Config, "REVOLUT_X_PRIVATE_KEY_PATH", None),
            pytest.raises(ValueError, match="REVOLUT_X_PRIVATE_KEY_PATH is not configured"),
        ):
            Config.get_private_key()

    def test_get_private_key_with_nonexistent_file_raises_error(self) -> None:
        """Test that getting private key with nonexistent file raises error."""
        with (
            patch.object(Config, "REVOLUT_X_PRIVATE_KEY_PATH", "/nonexistent/path.pem"),
            pytest.raises(FileNotFoundError, match="Private key not found"),
        ):
            Config.get_private_key()

    def test_get_private_key_success(self, tmp_path: Path) -> None:
        """Test successfully loading private key from file."""
        # Create a test key file
        key_file = tmp_path / "test_key.pem"
        test_key_data = b"test private key data"
        key_file.write_bytes(test_key_data)

        with patch.object(Config, "REVOLUT_X_PRIVATE_KEY_PATH", str(key_file)):
            key_data = Config.get_private_key()
            assert key_data == test_key_data
