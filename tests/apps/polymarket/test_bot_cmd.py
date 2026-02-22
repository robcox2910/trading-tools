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

    def test_bot_no_markets_or_series_exits_with_error(self) -> None:
        """Test that bot command exits with error when no markets or series specified."""
        runner = CliRunner()
        result = runner.invoke(app, ["bot", "--markets", ""])
        assert result.exit_code == 1
        assert "specify" in result.output

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

    def test_bot_with_series_discovery(self) -> None:
        """Test bot command with --series for auto-discovery."""
        runner = CliRunner()
        mock_result = _make_result()

        with patch("trading_tools.apps.polymarket.cli.bot_cmd.PolymarketClient") as mock_cls:
            # First client for discovery
            mock_discovery_client = AsyncMock()
            mock_discovery_client.__aenter__ = AsyncMock(return_value=mock_discovery_client)
            mock_discovery_client.__aexit__ = AsyncMock(return_value=None)
            mock_discovery_client.discover_series_markets = AsyncMock(
                return_value=[("cond_btc", "2026-02-22T12:05:00Z")]
            )

            # Second client for engine
            mock_engine_client = AsyncMock()
            mock_engine_client.__aenter__ = AsyncMock(return_value=mock_engine_client)
            mock_engine_client.__aexit__ = AsyncMock(return_value=None)

            mock_cls.return_value.__aenter__ = AsyncMock(
                side_effect=[mock_discovery_client, mock_engine_client]
            )
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            with patch(
                "trading_tools.apps.polymarket.cli.bot_cmd.PaperTradingEngine"
            ) as mock_engine_cls:
                mock_engine = AsyncMock()
                mock_engine.run = AsyncMock(return_value=mock_result)
                mock_engine_cls.return_value = mock_engine

                # Return different clients for discovery and engine
                mock_cls.side_effect = [
                    mock_discovery_client,
                    mock_engine_client,
                ]
                mock_discovery_client.__aenter__ = AsyncMock(return_value=mock_discovery_client)
                mock_engine_client.__aenter__ = AsyncMock(return_value=mock_engine_client)

                result = runner.invoke(
                    app,
                    [
                        "bot",
                        "--series",
                        "btc-updown-5m",
                        "--max-ticks",
                        "1",
                        "--poll-interval",
                        "0",
                    ],
                )

        assert result.exit_code == 0
        assert "Discovering" in result.output

    def test_bot_with_snipe_params(self) -> None:
        """Test bot command passes snipe parameters to strategy."""
        runner = CliRunner()
        mock_result = _make_result()

        with (
            patch("trading_tools.apps.polymarket.cli.bot_cmd.PolymarketClient") as mock_cls,
            patch(
                "trading_tools.apps.polymarket.cli.bot_cmd.PaperTradingEngine"
            ) as mock_engine_cls,
            patch("trading_tools.apps.polymarket.cli.bot_cmd.build_pm_strategy") as mock_build,
        ):
            mock_strategy = AsyncMock()
            mock_strategy.name = "pm_late_snipe_0.85_45s"
            mock_build.return_value = mock_strategy

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
                    "pm_late_snipe",
                    "--snipe-threshold",
                    "0.85",
                    "--snipe-window",
                    "45",
                    "--max-ticks",
                    "1",
                    "--poll-interval",
                    "0",
                ],
            )

        assert result.exit_code == 0
        # Verify snipe params were passed
        mock_build.assert_called_once()
        call_kwargs = mock_build.call_args
        expected_threshold = 0.85
        expected_window = 45
        assert call_kwargs.kwargs["snipe_threshold"] == expected_threshold
        assert call_kwargs.kwargs["snipe_window"] == expected_window

    def test_bot_crypto_5m_shortcut(self) -> None:
        """Test that --series crypto-5m expands to all 4 crypto series."""
        runner = CliRunner()
        mock_result = _make_result()

        with patch("trading_tools.apps.polymarket.cli.bot_cmd.PolymarketClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.discover_series_markets = AsyncMock(
                return_value=[
                    ("cond_btc", "2026-02-22T12:05:00Z"),
                    ("cond_eth", "2026-02-22T12:05:00Z"),
                ]
            )

            mock_engine_client = AsyncMock()
            mock_engine_client.__aenter__ = AsyncMock(return_value=mock_engine_client)
            mock_engine_client.__aexit__ = AsyncMock(return_value=None)

            mock_cls.side_effect = [mock_client, mock_engine_client]

            with patch(
                "trading_tools.apps.polymarket.cli.bot_cmd.PaperTradingEngine"
            ) as mock_engine_cls:
                mock_engine = AsyncMock()
                mock_engine.run = AsyncMock(return_value=mock_result)
                mock_engine_cls.return_value = mock_engine

                result = runner.invoke(
                    app,
                    [
                        "bot",
                        "--series",
                        "crypto-5m",
                        "--max-ticks",
                        "1",
                        "--poll-interval",
                        "0",
                    ],
                )

        assert result.exit_code == 0
        # Should have expanded crypto-5m into 4 slugs
        mock_client.discover_series_markets.assert_called_once()
        slugs = mock_client.discover_series_markets.call_args[0][0]
        expected_slug_count = 4
        assert len(slugs) == expected_slug_count
