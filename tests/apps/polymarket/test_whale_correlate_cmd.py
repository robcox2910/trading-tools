"""Tests for the whale-correlate CLI command."""

from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from trading_tools.apps.polymarket.cli import app
from trading_tools.apps.whale_monitor.models import WhaleTrade

_ADDRESS = "0xa45fe11dd1420fca906ceac2c067844379a42429"
_COLLECTED_AT = 1700000000000
_BASE_TS = 1700000000


@pytest.fixture
def runner() -> CliRunner:
    """Create a Typer CLI test runner."""
    return CliRunner()


def _make_trade(
    outcome: str = "Up",
    size: float = 50.0,
    price: float = 0.72,
    condition_id: str = "cond_a",
    tx_hash: str = "tx_001",
    title: str = "Bitcoin Up or Down - March 13, 6:30PM-6:45PM ET",
) -> WhaleTrade:
    """Create a WhaleTrade instance for CLI testing.

    Args:
        outcome: Outcome label.
        size: Token quantity.
        price: Execution price.
        condition_id: Market condition ID.
        tx_hash: Transaction hash.
        title: Market title.

    Returns:
        A WhaleTrade instance.

    """
    return WhaleTrade(
        whale_address=_ADDRESS,
        transaction_hash=tx_hash,
        side="BUY",
        asset_id="asset_test",
        condition_id=condition_id,
        size=size,
        price=price,
        timestamp=_BASE_TS,
        title=title,
        slug="btc-up-down",
        outcome=outcome,
        outcome_index=0,
        collected_at=_COLLECTED_AT,
    )


class TestWhaleCorrelateCommand:
    """Tests for the whale-correlate CLI command."""

    def test_no_trades_found(self, runner: CliRunner) -> None:
        """Display message when no trades exist for the address."""
        with patch(
            "trading_tools.apps.polymarket.cli.whale_correlate_cmd.WhaleRepository"
        ) as mock_repo_cls:
            mock_repo = AsyncMock()
            mock_repo.get_trades = AsyncMock(return_value=[])
            mock_repo_cls.return_value = mock_repo

            result = runner.invoke(
                app,
                [
                    "whale-correlate",
                    "--address",
                    _ADDRESS,
                    "--db-url",
                    "sqlite+aiosqlite:///test.db",
                ],
            )

        assert result.exit_code == 0
        assert "No trades found" in result.output

    def test_no_markets_after_filter(self, runner: CliRunner) -> None:
        """Display message when min-trades filters out all markets."""
        trades = [_make_trade(tx_hash="tx_1")]

        with patch(
            "trading_tools.apps.polymarket.cli.whale_correlate_cmd.WhaleRepository"
        ) as mock_repo_cls:
            mock_repo = AsyncMock()
            mock_repo.get_trades = AsyncMock(return_value=trades)
            mock_repo_cls.return_value = mock_repo

            result = runner.invoke(
                app,
                [
                    "whale-correlate",
                    "--address",
                    _ADDRESS,
                    "--min-trades",
                    "10",
                    "--db-url",
                    "sqlite+aiosqlite:///test.db",
                ],
            )

        assert result.exit_code == 0
        assert "No markets found" in result.output

    def test_displays_correlation(self, runner: CliRunner) -> None:
        """Display correlation report when trades and candles are available."""
        trades = [
            _make_trade(outcome="Up", size=100.0, price=0.60, tx_hash=f"tx_{i}") for i in range(15)
        ]

        with (
            patch(
                "trading_tools.apps.polymarket.cli.whale_correlate_cmd.WhaleRepository"
            ) as mock_repo_cls,
            patch(
                "trading_tools.apps.polymarket.cli.whale_correlate_cmd.BinanceClient"
            ) as mock_client_cls,
            patch(
                "trading_tools.apps.polymarket.cli.whale_correlate_cmd.BinanceCandleProvider"
            ) as mock_provider_cls,
        ):
            mock_repo = AsyncMock()
            mock_repo.get_trades = AsyncMock(return_value=trades)
            mock_repo_cls.return_value = mock_repo

            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            mock_provider = AsyncMock()
            mock_provider.get_candles = AsyncMock(return_value=[])
            mock_provider_cls.return_value = mock_provider

            result = runner.invoke(
                app,
                [
                    "whale-correlate",
                    "--address",
                    _ADDRESS,
                    "--min-trades",
                    "1",
                    "--db-url",
                    "sqlite+aiosqlite:///test.db",
                ],
            )

        assert result.exit_code == 0
        assert "Whale Spot Price Correlation" in result.output
