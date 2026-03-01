"""Tests for shared Polymarket CLI helpers."""

import os
from unittest.mock import patch

import pytest
import typer

from trading_tools.apps.polymarket.cli._helpers import (
    CRYPTO_5M_SERIES,
    CRYPTO_15M_SERIES,
    build_authenticated_client,
    configure_verbose_logging,
    parse_series_slugs,
)
from trading_tools.clients.polymarket.client import PolymarketClient


class TestParseSeriesSlugs:
    """Test series slug parsing and expansion."""

    def test_empty_string_returns_empty_tuple(self) -> None:
        """Return an empty tuple for an empty input string."""
        assert parse_series_slugs("") == ()

    def test_single_slug(self) -> None:
        """Parse a single slug without expansion."""
        assert parse_series_slugs("btc-updown-5m") == ("btc-updown-5m",)

    def test_comma_separated_slugs(self) -> None:
        """Parse multiple comma-separated slugs."""
        result = parse_series_slugs("btc-updown-5m,eth-updown-5m")
        assert result == ("btc-updown-5m", "eth-updown-5m")

    def test_crypto_5m_shortcut_expands(self) -> None:
        """Expand the crypto-5m shortcut to all four crypto series."""
        result = parse_series_slugs("crypto-5m")
        assert result == CRYPTO_5M_SERIES

    def test_crypto_5m_with_extra_slug(self) -> None:
        """Expand crypto-5m and append additional slugs."""
        result = parse_series_slugs("crypto-5m,custom-series")
        expected_len = len(CRYPTO_5M_SERIES) + 1
        assert len(result) == expected_len
        assert result[-1] == "custom-series"

    def test_whitespace_is_stripped(self) -> None:
        """Strip whitespace around slugs."""
        result = parse_series_slugs("  btc-updown-5m , eth-updown-5m  ")
        assert result == ("btc-updown-5m", "eth-updown-5m")

    def test_empty_segments_are_skipped(self) -> None:
        """Skip empty segments from trailing commas."""
        result = parse_series_slugs("btc-updown-5m,,eth-updown-5m,")
        assert result == ("btc-updown-5m", "eth-updown-5m")

    def test_crypto_15m_shortcut_expands(self) -> None:
        """Expand the crypto-15m shortcut to all four 15-minute crypto series."""
        result = parse_series_slugs("crypto-15m")
        assert result == CRYPTO_15M_SERIES

    def test_both_shortcuts_combined(self) -> None:
        """Expand both crypto-5m and crypto-15m shortcuts together."""
        result = parse_series_slugs("crypto-5m,crypto-15m")
        expected_len = len(CRYPTO_5M_SERIES) + len(CRYPTO_15M_SERIES)
        assert len(result) == expected_len
        assert result[: len(CRYPTO_5M_SERIES)] == CRYPTO_5M_SERIES
        assert result[len(CRYPTO_5M_SERIES) :] == CRYPTO_15M_SERIES


class TestConfigureVerboseLogging:
    """Test verbose logging configuration."""

    def test_configures_info_level(self) -> None:
        """Verify logging is configured at INFO level."""
        with patch("trading_tools.apps.polymarket.cli._helpers.logging") as mock_logging:
            configure_verbose_logging()
            mock_logging.basicConfig.assert_called_once_with(
                level=mock_logging.INFO,
                format="%(asctime)s %(message)s",
                datefmt="%H:%M:%S",
            )


class TestBuildAuthenticatedClient:
    """Test authenticated client construction from env vars."""

    def test_missing_private_key_exits(self) -> None:
        """Exit with code 1 when POLYMARKET_PRIVATE_KEY is not set."""
        with patch.dict(os.environ, {}, clear=True), pytest.raises(typer.Exit):
            build_authenticated_client()

    def test_returns_client_with_private_key(self) -> None:
        """Return a PolymarketClient when the private key is set."""
        env = {
            "POLYMARKET_PRIVATE_KEY": "0x" + "ab" * 32,
            "POLYMARKET_API_KEY": "key",
            "POLYMARKET_API_SECRET": "secret",
            "POLYMARKET_API_PASSPHRASE": "pass",
            "POLYMARKET_FUNDER_ADDRESS": "0x" + "cd" * 20,
        }
        with patch.dict(os.environ, env, clear=True):
            client = build_authenticated_client()
            assert isinstance(client, PolymarketClient)

    def test_optional_fields_default_to_none(self) -> None:
        """Optional API credentials default to None when not set."""
        env = {"POLYMARKET_PRIVATE_KEY": "0x" + "ab" * 32}
        with patch.dict(os.environ, env, clear=True):
            client = build_authenticated_client()
            assert isinstance(client, PolymarketClient)
