"""Tests for the whale-copy CLI command."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from trading_tools.apps.polymarket.cli import app

_ADDRESS = "0xa45fe11dd1420fca906ceac2c067844379a42429"
_CUSTOM_POLL_INTERVAL = 10
_CUSTOM_MIN_TRADES = 5


@pytest.fixture
def runner() -> CliRunner:
    """Create a Typer CLI test runner."""
    return CliRunner()


class TestWhaleCopyCommand:
    """Tests for the whale-copy CLI command."""

    def test_paper_mode_by_default(self, runner: CliRunner) -> None:
        """Run in paper mode when --confirm-live is not passed."""
        with (
            patch(
                "trading_tools.apps.polymarket.cli.whale_copy_cmd.WhaleRepository"
            ) as mock_repo_cls,
            patch(
                "trading_tools.apps.polymarket.cli.whale_copy_cmd.WhaleCopyTrader"
            ) as mock_trader_cls,
        ):
            mock_repo = AsyncMock()
            mock_repo_cls.return_value = mock_repo

            mock_trader = AsyncMock()
            mock_trader.run = AsyncMock()
            mock_trader_cls.return_value = mock_trader

            result = runner.invoke(
                app,
                [
                    "whale-copy",
                    "--address",
                    _ADDRESS,
                    "--db-url",
                    "sqlite+aiosqlite:///test.db",
                ],
            )

        assert result.exit_code == 0
        mock_trader_cls.assert_called_once()
        call_kwargs = mock_trader_cls.call_args[1]
        assert call_kwargs["live"] is False
        assert call_kwargs["client"] is None

    def test_confirm_live_shows_warning(self, runner: CliRunner) -> None:
        """Display a warning banner when --confirm-live is passed."""
        with (
            patch(
                "trading_tools.apps.polymarket.cli.whale_copy_cmd.WhaleRepository"
            ) as mock_repo_cls,
            patch(
                "trading_tools.apps.polymarket.cli.whale_copy_cmd.WhaleCopyTrader"
            ) as mock_trader_cls,
            patch(
                "trading_tools.apps.polymarket.cli.whale_copy_cmd.build_authenticated_client"
            ) as mock_build,
            patch("trading_tools.apps.polymarket.cli.whale_copy_cmd.time"),
        ):
            mock_repo = AsyncMock()
            mock_repo_cls.return_value = mock_repo

            mock_client = AsyncMock()
            mock_build.return_value = mock_client

            mock_trader = AsyncMock()
            mock_trader.run = AsyncMock()
            mock_trader_cls.return_value = mock_trader

            result = runner.invoke(
                app,
                [
                    "whale-copy",
                    "--address",
                    _ADDRESS,
                    "--confirm-live",
                    "--db-url",
                    "sqlite+aiosqlite:///test.db",
                ],
            )

        assert result.exit_code == 0
        assert "LIVE TRADING MODE" in result.output
        call_kwargs = mock_trader_cls.call_args[1]
        assert call_kwargs["live"] is True
        assert call_kwargs["client"] is mock_client

    def test_custom_config_options(self, runner: CliRunner) -> None:
        """Pass custom config options through to WhaleCopyConfig."""
        with (
            patch(
                "trading_tools.apps.polymarket.cli.whale_copy_cmd.WhaleRepository"
            ) as mock_repo_cls,
            patch(
                "trading_tools.apps.polymarket.cli.whale_copy_cmd.WhaleCopyTrader"
            ) as mock_trader_cls,
        ):
            mock_repo = AsyncMock()
            mock_repo_cls.return_value = mock_repo

            mock_trader = AsyncMock()
            mock_trader.run = AsyncMock()
            mock_trader_cls.return_value = mock_trader

            result = runner.invoke(
                app,
                [
                    "whale-copy",
                    "--address",
                    _ADDRESS,
                    "--poll-interval",
                    str(_CUSTOM_POLL_INTERVAL),
                    "--min-bias",
                    "2.0",
                    "--min-trades",
                    str(_CUSTOM_MIN_TRADES),
                    "--capital",
                    "500",
                    "--db-url",
                    "sqlite+aiosqlite:///test.db",
                ],
            )

        assert result.exit_code == 0
        call_kwargs = mock_trader_cls.call_args[1]
        config = call_kwargs["config"]
        assert config.poll_interval == _CUSTOM_POLL_INTERVAL
        assert config.min_trades == _CUSTOM_MIN_TRADES
