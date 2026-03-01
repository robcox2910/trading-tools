"""Tests for the tick-collect CLI command."""

from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from trading_tools.apps.polymarket.cli import app
from trading_tools.apps.polymarket.cli._helpers import parse_series_slugs

_CONDITION_ID = "cond_cli_test_tick"
_CRYPTO_5M_SLUG_COUNT = 4


class TestTickCollectCommand:
    """Tests for the tick-collect CLI command."""

    def test_no_markets_or_series_exits_with_error(self) -> None:
        """Exit with error when neither --markets nor --series is specified."""
        runner = CliRunner()
        result = runner.invoke(app, ["tick-collect"])

        assert result.exit_code == 1
        assert "specify" in result.output

    def test_empty_markets_and_series_exits_with_error(self) -> None:
        """Exit with error when both --markets and --series are empty strings."""
        runner = CliRunner()
        result = runner.invoke(
            app,
            ["tick-collect", "--markets", "", "--series", ""],
        )

        assert result.exit_code == 1

    def test_markets_flag_starts_collector(self) -> None:
        """Verify --markets flag creates and runs the collector."""
        runner = CliRunner()

        with patch(
            "trading_tools.apps.polymarket.cli.tick_collector_cmd.TickCollector"
        ) as mock_cls:
            mock_collector = AsyncMock()
            mock_collector.run = AsyncMock()
            mock_cls.return_value = mock_collector

            result = runner.invoke(
                app,
                ["tick-collect", "--markets", _CONDITION_ID],
            )

        assert "Starting tick collector" in result.output
        mock_cls.assert_called_once()

    def test_series_flag_starts_collector(self) -> None:
        """Verify --series flag creates and runs the collector."""
        runner = CliRunner()

        with patch(
            "trading_tools.apps.polymarket.cli.tick_collector_cmd.TickCollector"
        ) as mock_cls:
            mock_collector = AsyncMock()
            mock_collector.run = AsyncMock()
            mock_cls.return_value = mock_collector

            result = runner.invoke(
                app,
                ["tick-collect", "--series", "btc-updown-5m"],
            )

        assert "Starting tick collector" in result.output

    def test_db_url_flag_passed_to_config(self) -> None:
        """Verify --db-url flag is forwarded to the CollectorConfig."""
        runner = CliRunner()
        custom_url = "sqlite+aiosqlite:///:memory:"

        with patch(
            "trading_tools.apps.polymarket.cli.tick_collector_cmd.TickCollector"
        ) as mock_cls:
            mock_collector = AsyncMock()
            mock_collector.run = AsyncMock()
            mock_cls.return_value = mock_collector

            result = runner.invoke(
                app,
                [
                    "tick-collect",
                    "--markets",
                    _CONDITION_ID,
                    "--db-url",
                    custom_url,
                ],
            )

        assert result.exit_code == 0
        config_arg = mock_cls.call_args[0][0]
        assert config_arg.db_url == custom_url


class TestParseSeriesSlugs:
    """Tests for the series slug parser."""

    def test_parse_single_slug(self) -> None:
        """Parse a single series slug."""
        result = parse_series_slugs("btc-updown-5m")

        assert result == ("btc-updown-5m",)

    def test_parse_multiple_slugs(self) -> None:
        """Parse comma-separated slugs."""
        result = parse_series_slugs("btc-updown-5m,eth-updown-5m")

        assert result == ("btc-updown-5m", "eth-updown-5m")

    def test_expand_crypto_5m_shortcut(self) -> None:
        """Expand the crypto-5m shortcut into all four series."""
        result = parse_series_slugs("crypto-5m")

        assert len(result) == _CRYPTO_5M_SLUG_COUNT
        assert "btc-updown-5m" in result
        assert "eth-updown-5m" in result
        assert "sol-updown-5m" in result
        assert "xrp-updown-5m" in result

    def test_empty_string(self) -> None:
        """Parse an empty string returns empty tuple."""
        result = parse_series_slugs("")

        assert result == ()

    def test_strips_whitespace(self) -> None:
        """Strip whitespace around slug values."""
        result = parse_series_slugs("  btc-updown-5m , eth-updown-5m  ")

        assert result == ("btc-updown-5m", "eth-updown-5m")
