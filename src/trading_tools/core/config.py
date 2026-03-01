"""Configuration management for trading tools."""

import os
import re
from pathlib import Path
from typing import Any, cast

import yaml
from dotenv import load_dotenv


class ConfigError(Exception):
    """Raise when configuration loading or validation fails."""


class ConfigLoader:
    """Load and manage configuration from YAML files with environment variable substitution."""

    def __init__(self, config_dir: Path | None = None) -> None:
        """Initialize the config loader.

        Load environment variables from a ``.env`` file (if present) and
        then read YAML configuration from the given directory.

        Args:
            config_dir: Directory containing config files. Defaults to src/trading_tools/config.

        """
        load_dotenv()
        if config_dir is None:
            # Default to config directory relative to this file
            config_dir = Path(__file__).parent.parent / "config"
        self.config_dir = Path(config_dir)
        self._config: dict[str, Any] = {}
        self._load_config()

    def _load_config(self) -> None:
        """Load configuration from YAML files."""
        # Load base settings
        settings_file = self.config_dir / "settings.yaml"
        if settings_file.exists():
            with settings_file.open() as f:
                self._config = yaml.safe_load(f) or {}

        # Override with local settings if exists
        local_settings = self.config_dir / "settings.local.yaml"
        if local_settings.exists():
            with local_settings.open() as f:
                local_config = cast("dict[str, Any]", yaml.safe_load(f) or {})
                self._deep_merge(self._config, local_config)

        # Substitute environment variables
        self._config = self._substitute_env_vars(self._config)

    def _deep_merge(self, base: dict[str, Any], override: dict[str, Any]) -> None:
        """Deep merge override dict into base dict.

        Args:
            base: Base dictionary to merge into (modified in place).
            override: Dictionary with values to override.

        """
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_merge(base[key], cast("dict[str, Any]", value))
            else:
                base[key] = value

    def _substitute_env_vars(self, config: Any) -> Any:
        """Recursively substitute environment variables in config.

        Supports format: ${VAR_NAME:default_value} or ${VAR_NAME}

        Args:
            config: Configuration value (dict, list, or str).

        Returns:
            Configuration with environment variables substituted.

        """
        if isinstance(config, dict):
            return {
                k: self._substitute_env_vars(v)
                for k, v in config.items()  # pyright: ignore[reportUnknownVariableType]
            }
        if isinstance(config, list):
            return [
                self._substitute_env_vars(item)
                for item in config  # pyright: ignore[reportUnknownVariableType]
            ]
        if isinstance(config, str) and config.startswith("${") and config.endswith("}"):
            # Extract variable name and default value
            var_expr = config[2:-1]  # Remove ${ and }
            if ":" in var_expr:
                var_name, default = var_expr.split(":", 1)
            else:
                var_name, default = var_expr, None

            value = os.getenv(var_name, default)
            if value is None:
                msg = f"Required environment variable ${{{var_name}}} is not set and has no default"
                raise ConfigError(msg)
            return value

        if isinstance(config, str) and re.search(r"\$\{[^}]+\}", config):
            msg = f"Unresolved environment variable reference in: {config}"
            raise ConfigError(msg)

        return config

    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value by dot-notation key.

        Args:
            key: Configuration key in dot notation (e.g., 'revolut_x.api_key').
            default: Default value if key not found.

        Returns:
            Configuration value.

        """
        keys = key.split(".")
        current: Any = self._config
        for k in keys:
            if isinstance(current, dict):
                current = cast("dict[str, Any]", current).get(k)
                if current is None:
                    return default
            else:
                return default
        return current  # pyright: ignore[reportReturnType]

    def get_revolut_x_config(self) -> dict[str, Any]:
        """Get Revolut X API configuration.

        Returns:
            Dictionary with Revolut X settings.

        Raises:
            ConfigError: If the revolut_x config value is not a dictionary.

        """
        result: Any = self.get("revolut_x", {})
        if isinstance(result, dict):
            return cast("dict[str, Any]", result)
        msg = f"revolut_x config must be a dict, got {type(result).__name__}"
        raise ConfigError(msg)

    def get_private_key(self) -> bytes:
        """Load the Ed25519 private key from file.

        Returns:
            The private key bytes.

        Raises:
            ValueError: If private key path is not configured.
            FileNotFoundError: If private key file doesn't exist.

        """
        key_path = self.get("revolut_x.private_key_path")
        if not key_path:
            raise ValueError("revolut_x.private_key_path is not configured")

        path = Path(key_path)
        if not path.exists():
            raise FileNotFoundError(f"Private key not found at {path}")

        return path.read_bytes()


_config: ConfigLoader | None = None


def get_config() -> ConfigLoader:
    """Return the global ``ConfigLoader`` singleton, creating it on first use.

    Lazy initialisation avoids side effects (file I/O, ``load_dotenv``)
    at import time and makes testing easier.

    Returns:
        The shared ``ConfigLoader`` instance.

    """
    global _config  # noqa: PLW0603
    if _config is None:
        _config = ConfigLoader()
    return _config
