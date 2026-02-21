"""Tests for Polymarket CLI commands."""

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from trading_tools.apps.polymarket.cli import app
from trading_tools.clients.polymarket.exceptions import PolymarketAPIError
from trading_tools.clients.polymarket.models import (
    Market,
    MarketToken,
    OrderBook,
    OrderLevel,
)

_PRICE_YES = Decimal("0.72")
_PRICE_NO = Decimal("0.28")
_VOLUME = Decimal(50000)
_LIQUIDITY = Decimal(10000)
_YES_TOKEN_ID = "t1"
_NO_TOKEN_ID = "t2"
_BOOK_TOKEN_ID = "token123"


@pytest.fixture
def runner() -> CliRunner:
    """Create a Typer CLI test runner."""
    return CliRunner()


def _make_market(
    condition_id: str = "cond1",
    question: str = "Will Bitcoin reach $100K?",
) -> Market:
    """Create a Market instance for testing.

    Args:
        condition_id: Market condition identifier.
        question: Market question text.

    Returns:
        Market instance with test data.

    """
    return Market(
        condition_id=condition_id,
        question=question,
        description="Test description",
        tokens=(
            MarketToken(token_id=_YES_TOKEN_ID, outcome="Yes", price=_PRICE_YES),
            MarketToken(token_id=_NO_TOKEN_ID, outcome="No", price=_PRICE_NO),
        ),
        end_date="2026-03-31",
        volume=_VOLUME,
        liquidity=_LIQUIDITY,
        active=True,
    )


def _make_order_book() -> OrderBook:
    """Create an OrderBook instance for testing.

    Returns:
        OrderBook with sample bid and ask levels.

    """
    return OrderBook(
        token_id=_BOOK_TOKEN_ID,
        bids=(
            OrderLevel(price=Decimal("0.70"), size=Decimal(100)),
            OrderLevel(price=Decimal("0.69"), size=Decimal(200)),
        ),
        asks=(
            OrderLevel(price=Decimal("0.73"), size=Decimal(150)),
            OrderLevel(price=Decimal("0.74"), size=Decimal(50)),
        ),
        spread=Decimal("0.03"),
        midpoint=Decimal("0.715"),
    )


class TestMarketsCommand:
    """Test suite for the markets CLI command."""

    def test_markets_displays_results(self, runner: CliRunner) -> None:
        """Test markets command displays matching markets."""
        markets = [_make_market()]

        with patch("trading_tools.apps.polymarket.cli.markets_cmd.PolymarketClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.search_markets = AsyncMock(return_value=markets)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_cls.return_value = mock_client

            result = runner.invoke(app, ["markets", "--keyword", "Bitcoin"])

        assert result.exit_code == 0
        assert "Bitcoin" in result.output
        assert "0.72" in result.output
        assert "0.28" in result.output

    def test_markets_no_results(self, runner: CliRunner) -> None:
        """Test markets command shows message when no results found."""
        with patch("trading_tools.apps.polymarket.cli.markets_cmd.PolymarketClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.search_markets = AsyncMock(return_value=[])
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_cls.return_value = mock_client

            result = runner.invoke(app, ["markets", "--keyword", "Nonexistent"])

        assert result.exit_code == 0
        assert "No markets found" in result.output


class TestOddsCommand:
    """Test suite for the odds CLI command."""

    def test_odds_displays_market(self, runner: CliRunner) -> None:
        """Test odds command displays market details."""
        market = _make_market()

        with patch("trading_tools.apps.polymarket.cli.odds_cmd.PolymarketClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.get_market = AsyncMock(return_value=market)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_cls.return_value = mock_client

            result = runner.invoke(app, ["odds", "cond1"])

        assert result.exit_code == 0
        assert "Will Bitcoin reach $100K?" in result.output
        assert "0.7200" in result.output
        assert "72.0%" in result.output

    def test_odds_error_handling(self, runner: CliRunner) -> None:
        """Test odds command handles API errors gracefully."""
        with patch("trading_tools.apps.polymarket.cli.odds_cmd.PolymarketClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.get_market = AsyncMock(
                side_effect=PolymarketAPIError(msg="Not found", status_code=404)
            )
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_cls.return_value = mock_client

            result = runner.invoke(app, ["odds", "bad_id"])

        assert result.exit_code == 1


class TestBookCommand:
    """Test suite for the book CLI command."""

    def test_book_displays_order_book(self, runner: CliRunner) -> None:
        """Test book command displays order book data."""
        order_book = _make_order_book()

        with patch("trading_tools.apps.polymarket.cli.book_cmd.PolymarketClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.get_order_book = AsyncMock(return_value=order_book)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_cls.return_value = mock_client

            result = runner.invoke(app, ["book", "token123"])

        assert result.exit_code == 0
        assert "token123" in result.output
        assert "0.0300" in result.output  # spread
        assert "0.7150" in result.output  # midpoint
        assert "0.7000" in result.output  # best bid

    def test_book_error_handling(self, runner: CliRunner) -> None:
        """Test book command handles API errors gracefully."""
        with patch("trading_tools.apps.polymarket.cli.book_cmd.PolymarketClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.get_order_book = AsyncMock(
                side_effect=PolymarketAPIError(msg="Failed", status_code=500)
            )
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_cls.return_value = mock_client

            result = runner.invoke(app, ["book", "bad_token"])

        assert result.exit_code == 1

    def test_book_respects_depth(self, runner: CliRunner) -> None:
        """Test book command limits displayed levels to depth parameter."""
        order_book = _make_order_book()

        with patch("trading_tools.apps.polymarket.cli.book_cmd.PolymarketClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.get_order_book = AsyncMock(return_value=order_book)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_cls.return_value = mock_client

            result = runner.invoke(app, ["book", "token123", "--depth", "1"])

        assert result.exit_code == 0
        # Should show only 1 level (best bid 0.70, best ask 0.73)
        assert "0.7000" in result.output
        assert "0.7300" in result.output
        # Second level should not appear
        assert "0.6900" not in result.output
