"""Configuration management for trading tools."""

import os
from pathlib import Path

from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


class Config:
    """Application configuration."""

    # Revolut X API settings
    REVOLUT_X_API_KEY: str | None = os.getenv("REVOLUT_X_API_KEY")
    REVOLUT_X_PRIVATE_KEY_PATH: str | None = os.getenv("REVOLUT_X_PRIVATE_KEY_PATH")
    REVOLUT_X_BASE_URL: str = os.getenv("REVOLUT_X_BASE_URL", "https://api.revolut.com/api/1.0")
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "sandbox")

    @classmethod
    def get_private_key(cls) -> bytes:
        """Load the Ed25519 private key from file.

        Returns:
            The private key bytes.

        Raises:
            ValueError: If private key path is not configured.
            FileNotFoundError: If private key file doesn't exist.
        """
        if not cls.REVOLUT_X_PRIVATE_KEY_PATH:
            raise ValueError("REVOLUT_X_PRIVATE_KEY_PATH is not configured")

        key_path = Path(cls.REVOLUT_X_PRIVATE_KEY_PATH)
        if not key_path.exists():
            raise FileNotFoundError(f"Private key not found at {key_path}")

        return key_path.read_bytes()


config = Config()
