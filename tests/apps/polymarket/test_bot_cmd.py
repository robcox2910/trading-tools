"""Tests for the Polymarket bot CLI command."""

from decimal import Decimal
from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from trading_tools.apps.polymarket.cli import app
from trading_tools.apps.polymarket_bot.models import PaperTradingResult

_CONDITION_ID = "cond_cli_test"


def _make_result() -> PaperTradingResult:
    """Create a sample PaperTradingResult for testing.

    Returns:
        PaperTradingResult with sample data.

    """
    return PaperTradingResult(
        strategy_name="pm_mean_reversion_20_1.5",
        initial_capital=Decimal(1000),
        final_capital=Decimal(1050),
        trades=(),
        snapshots_processed=5,
        metrics={
            "total_return": Decimal("0.05"),
            "total_trades": Decimal(0),
        },
    )


class TestBotCommand:
    """Tests for the bot CLI command."""

    def test_bot_no_markets_exits_with_error(self) -> None:
        """Test that bot command exits with error when no markets specified."""
        runner = CliRunner()
        result = runner.invoke(app, ["bot", "--markets", ""])
        assert result.exit_code == 1
        assert "must specify" in result.output

    def test_bot_unknown_strategy_exits_with_error(self) -> None:
        """Test that bot command exits with error for unknown strategy."""
        runner = CliRunner()
        result = runner.invoke(
            app,
            ["bot", "--markets", _CONDITION_ID, "--strategy", "bad_strategy"],
        )
        assert result.exit_code == 1
        assert "Error" in result.output

    def test_bot_displays_results(self) -> None:
        """Test that bot command displays results on successful run."""
        runner = CliRunner()
        mock_result = _make_result()

        with (
            patch("trading_tools.apps.polymarket.cli.bot_cmd.PolymarketClient") as mock_cls,
            patch(
                "trading_tools.apps.polymarket.cli.bot_cmd.PaperTradingEngine"
            ) as mock_engine_cls,
        ):
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_cls.return_value = mock_client

            mock_engine = AsyncMock()
            mock_engine.run = AsyncMock(return_value=mock_result)
            mock_engine_cls.return_value = mock_engine

            result = runner.invoke(
                app,
                [
                    "bot",
                    "--markets",
                    _CONDITION_ID,
                    "--max-ticks",
                    "5",
                    "--poll-interval",
                    "0",
                ],
            )

        assert result.exit_code == 0
        assert "Paper Trading Results" in result.output
        assert "pm_mean_reversion" in result.output
        assert "1050" in result.output

    def test_bot_with_custom_strategy(self) -> None:
        """Test bot command with a custom strategy name."""
        runner = CliRunner()
        mock_result = _make_result()

        with (
            patch("trading_tools.apps.polymarket.cli.bot_cmd.PolymarketClient") as mock_cls,
            patch(
                "trading_tools.apps.polymarket.cli.bot_cmd.PaperTradingEngine"
            ) as mock_engine_cls,
        ):
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_cls.return_value = mock_client

            mock_engine = AsyncMock()
            mock_engine.run = AsyncMock(return_value=mock_result)
            mock_engine_cls.return_value = mock_engine

            result = runner.invoke(
                app,
                [
                    "bot",
                    "--markets",
                    _CONDITION_ID,
                    "--strategy",
                    "pm_liquidity_imbalance",
                    "--max-ticks",
                    "1",
                    "--poll-interval",
                    "0",
                ],
            )

        assert result.exit_code == 0

    def test_bot_multiple_markets(self) -> None:
        """Test bot command with multiple comma-separated markets."""
        runner = CliRunner()
        mock_result = _make_result()

        with (
            patch("trading_tools.apps.polymarket.cli.bot_cmd.PolymarketClient") as mock_cls,
            patch(
                "trading_tools.apps.polymarket.cli.bot_cmd.PaperTradingEngine"
            ) as mock_engine_cls,
        ):
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_cls.return_value = mock_client

            mock_engine = AsyncMock()
            mock_engine.run = AsyncMock(return_value=mock_result)
            mock_engine_cls.return_value = mock_engine

            result = runner.invoke(
                app,
                [
                    "bot",
                    "--markets",
                    "cond1,cond2,cond3",
                    "--max-ticks",
                    "1",
                    "--poll-interval",
                    "0",
                ],
            )

        assert result.exit_code == 0
        assert "cond1" in result.output
        assert "cond2" in result.output
