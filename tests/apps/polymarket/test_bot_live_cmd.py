"""Tests for the Polymarket bot-live CLI command.

Verify the bot-live subcommand requires --confirm-live, builds an
authenticated client, and displays results using mocked clients so
no real trades are placed.
"""

from decimal import Decimal
from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from trading_tools.apps.polymarket.cli import app
from trading_tools.apps.polymarket_bot.models import LiveTradingResult
from trading_tools.clients.polymarket.models import Balance

_CONDITION_ID = "cond_cli_live_test"
_INITIAL_BALANCE = Decimal("1000.00")


def _make_result() -> LiveTradingResult:
    """Create a sample LiveTradingResult for testing.

    Returns:
        LiveTradingResult with sample data.

    """
    return LiveTradingResult(
        strategy_name="pm_late_snipe_0.80_60s",
        initial_balance=_INITIAL_BALANCE,
        final_balance=Decimal("1050.00"),
        trades=(),
        snapshots_processed=5,
        metrics={
            "total_return": Decimal("0.05"),
            "total_trades": Decimal(0),
        },
    )


class TestBotLiveCommand:
    """Tests for the bot-live CLI command."""

    def test_bot_live_requires_confirm_live(self) -> None:
        """Verify bot-live exits with error without --confirm-live."""
        runner = CliRunner()
        result = runner.invoke(
            app,
            ["bot-live", "--markets", _CONDITION_ID],
        )
        assert result.exit_code == 1
        assert "--confirm-live" in result.output

    def test_bot_live_no_markets_or_series_exits_with_error(self) -> None:
        """Verify bot-live exits with error when no markets or series specified."""
        runner = CliRunner()
        with patch(
            "trading_tools.apps.polymarket.cli.bot_live_cmd._build_authenticated_client",
            return_value=AsyncMock(),
        ):
            result = runner.invoke(
                app,
                ["bot-live", "--markets", "", "--confirm-live"],
            )
        assert result.exit_code == 1
        assert "specify" in result.output

    def test_bot_live_unknown_strategy_exits_with_error(self) -> None:
        """Verify bot-live exits with error for unknown strategy."""
        runner = CliRunner()
        with patch(
            "trading_tools.apps.polymarket.cli.bot_live_cmd._build_authenticated_client",
            return_value=AsyncMock(),
        ):
            result = runner.invoke(
                app,
                [
                    "bot-live",
                    "--markets",
                    _CONDITION_ID,
                    "--strategy",
                    "bad_strategy",
                    "--confirm-live",
                ],
            )
        assert result.exit_code == 1
        assert "Error" in result.output

    def test_bot_live_displays_warning_banner_and_results(self) -> None:
        """Verify bot-live displays warning banner and results on successful run."""
        runner = CliRunner()
        mock_result = _make_result()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get_balance = AsyncMock(
            return_value=Balance(
                asset_type="COLLATERAL",
                balance=_INITIAL_BALANCE,
                allowance=Decimal(10000),
            ),
        )

        with (
            patch(
                "trading_tools.apps.polymarket.cli.bot_live_cmd._build_authenticated_client",
                return_value=mock_client,
            ),
            patch(
                "trading_tools.apps.polymarket.cli.bot_live_cmd.LiveTradingEngine",
            ) as mock_engine_cls,
        ):
            mock_engine = AsyncMock()
            mock_engine.run = AsyncMock(return_value=mock_result)
            mock_engine_cls.return_value = mock_engine

            result = runner.invoke(
                app,
                [
                    "bot-live",
                    "--markets",
                    _CONDITION_ID,
                    "--max-ticks",
                    "5",
                    "--confirm-live",
                ],
            )

        assert result.exit_code == 0
        assert "LIVE TRADING MODE" in result.output
        assert "real money at risk" in result.output
        assert "Live Trading Results" in result.output
        assert "1050" in result.output

    def test_bot_live_displays_initial_balance(self) -> None:
        """Verify bot-live shows USDC balance before starting."""
        runner = CliRunner()
        mock_result = _make_result()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get_balance = AsyncMock(
            return_value=Balance(
                asset_type="COLLATERAL",
                balance=_INITIAL_BALANCE,
                allowance=Decimal(10000),
            ),
        )

        with (
            patch(
                "trading_tools.apps.polymarket.cli.bot_live_cmd._build_authenticated_client",
                return_value=mock_client,
            ),
            patch(
                "trading_tools.apps.polymarket.cli.bot_live_cmd.LiveTradingEngine",
            ) as mock_engine_cls,
        ):
            mock_engine = AsyncMock()
            mock_engine.run = AsyncMock(return_value=mock_result)
            mock_engine_cls.return_value = mock_engine

            result = runner.invoke(
                app,
                [
                    "bot-live",
                    "--markets",
                    _CONDITION_ID,
                    "--max-ticks",
                    "3",
                    "--confirm-live",
                ],
            )

        assert result.exit_code == 0
        assert "USDC Balance: 1000" in result.output
