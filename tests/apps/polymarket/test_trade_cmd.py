"""Tests for the Polymarket trade CLI commands.

Verify the trade, balance, orders, and cancel subcommands using mocked
clients so no real trades are placed.
"""

from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from trading_tools.apps.polymarket.cli import app
from trading_tools.clients.polymarket.exceptions import PolymarketAPIError
from trading_tools.clients.polymarket.models import (
    Balance,
    Market,
    MarketToken,
    OrderBook,
    OrderLevel,
    OrderResponse,
)

_YES_TOKEN_ID = "token_yes_123"
_NO_TOKEN_ID = "token_no_456"
_CONDITION_ID = "0xabc123"
_ORDER_ID = "order_789"
_PRICE_YES = Decimal("0.65")
_PRICE_NO = Decimal("0.35")
_BALANCE_AMOUNT = Decimal("1000.00")
_ALLOWANCE_AMOUNT = Decimal("500.00")
_FILLED_ZERO = Decimal(0)
_ORDER_SIZE = Decimal("50.00")
_ORDER_PRICE = Decimal("0.65")
_EXPECTED_OPEN_ORDERS = 2


@pytest.fixture
def runner() -> CliRunner:
    """Create a Typer CLI test runner."""
    return CliRunner()


def _make_market() -> Market:
    """Create a test Market with YES/NO tokens.

    Returns:
        Market instance with standard test data.

    """
    return Market(
        condition_id=_CONDITION_ID,
        question="Will BTC reach $100K?",
        description="Test market",
        tokens=(
            MarketToken(token_id=_YES_TOKEN_ID, outcome="Yes", price=_PRICE_YES),
            MarketToken(token_id=_NO_TOKEN_ID, outcome="No", price=_PRICE_NO),
        ),
        end_date="2026-03-31",
        volume=Decimal(50000),
        liquidity=Decimal(10000),
        active=True,
    )


def _make_order_book() -> OrderBook:
    """Create a test OrderBook with bids and asks.

    Returns:
        OrderBook with sample levels.

    """
    return OrderBook(
        token_id=_YES_TOKEN_ID,
        bids=(OrderLevel(price=Decimal("0.64"), size=Decimal(100)),),
        asks=(OrderLevel(price=Decimal("0.66"), size=Decimal(150)),),
        spread=Decimal("0.02"),
        midpoint=Decimal("0.65"),
    )


def _make_order_response(
    order_id: str = _ORDER_ID,
    status: str = "live",
) -> OrderResponse:
    """Create a test OrderResponse.

    Args:
        order_id: Order identifier.
        status: Order status string.

    Returns:
        OrderResponse with standard test data.

    """
    return OrderResponse(
        order_id=order_id,
        status=status,
        token_id=_YES_TOKEN_ID,
        side="BUY",
        price=_ORDER_PRICE,
        size=_ORDER_SIZE,
        filled=_FILLED_ZERO,
    )


def _mock_client(
    *,
    market: Market | None = None,
    order_book: OrderBook | None = None,
    order_response: OrderResponse | None = None,
    balance: Balance | None = None,
    open_orders: list[OrderResponse] | None = None,
    cancel_result: dict[str, Any] | None = None,
) -> AsyncMock:
    """Build a fully mocked PolymarketClient.

    Configure return values for the methods used by trade CLI commands.

    Args:
        market: Market to return from get_market.
        order_book: OrderBook to return from get_order_book.
        order_response: Response to return from place_order.
        balance: Balance to return from get_balance.
        open_orders: List to return from get_open_orders.
        cancel_result: Dict to return from cancel_order.

    Returns:
        AsyncMock configured as a PolymarketClient.

    """
    mock = AsyncMock()
    mock.get_market = AsyncMock(return_value=market or _make_market())
    mock.get_order_book = AsyncMock(return_value=order_book or _make_order_book())
    mock.place_order = AsyncMock(return_value=order_response or _make_order_response())
    mock.get_balance = AsyncMock(
        return_value=balance
        or Balance(asset_type="COLLATERAL", balance=_BALANCE_AMOUNT, allowance=_ALLOWANCE_AMOUNT),
    )
    mock.get_open_orders = AsyncMock(return_value=open_orders or [])
    mock.cancel_order = AsyncMock(return_value=cancel_result or {"status": "cancelled"})
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=None)
    return mock


class TestTradeCommand:
    """Test suite for the trade CLI command."""

    def test_trade_limit_order_with_confirm(self, runner: CliRunner) -> None:
        """Place a limit order with user confirmation."""
        mock = _mock_client()
        with patch(
            "trading_tools.apps.polymarket.cli.trade_cmd.build_authenticated_client",
            return_value=mock,
        ):
            result = runner.invoke(
                app,
                [
                    "trade",
                    "--condition-id",
                    _CONDITION_ID,
                    "--side",
                    "buy",
                    "--outcome",
                    "yes",
                    "--amount",
                    "50",
                    "--price",
                    "0.65",
                    "--type",
                    "limit",
                ],
                input="y\n",
            )

        assert result.exit_code == 0
        assert "Order Preview" in result.output
        assert _ORDER_ID in result.output
        mock.place_order.assert_awaited_once()

    def test_trade_cancelled_by_user(self, runner: CliRunner) -> None:
        """Abort trade when user declines confirmation."""
        mock = _mock_client()
        with patch(
            "trading_tools.apps.polymarket.cli.trade_cmd.build_authenticated_client",
            return_value=mock,
        ):
            result = runner.invoke(
                app,
                [
                    "trade",
                    "--condition-id",
                    _CONDITION_ID,
                    "--side",
                    "buy",
                    "--outcome",
                    "yes",
                    "--amount",
                    "50",
                    "--price",
                    "0.65",
                    "--type",
                    "limit",
                ],
                input="n\n",
            )

        assert "cancelled" in result.output.lower()
        mock.place_order.assert_not_awaited()

    def test_trade_no_confirm_flag(self, runner: CliRunner) -> None:
        """Place order without confirmation when --no-confirm is set."""
        mock = _mock_client()
        with patch(
            "trading_tools.apps.polymarket.cli.trade_cmd.build_authenticated_client",
            return_value=mock,
        ):
            result = runner.invoke(
                app,
                [
                    "trade",
                    "--condition-id",
                    _CONDITION_ID,
                    "--side",
                    "buy",
                    "--outcome",
                    "yes",
                    "--amount",
                    "50",
                    "--price",
                    "0.65",
                    "--type",
                    "limit",
                    "--no-confirm",
                ],
            )

        assert result.exit_code == 0
        assert "WARNING" in result.output
        assert _ORDER_ID in result.output
        mock.place_order.assert_awaited_once()

    def test_trade_market_order(self, runner: CliRunner) -> None:
        """Place a market (FOK) order."""
        mock = _mock_client()
        with patch(
            "trading_tools.apps.polymarket.cli.trade_cmd.build_authenticated_client",
            return_value=mock,
        ):
            result = runner.invoke(
                app,
                [
                    "trade",
                    "--condition-id",
                    _CONDITION_ID,
                    "--side",
                    "sell",
                    "--outcome",
                    "no",
                    "--amount",
                    "25",
                    "--price",
                    "0.35",
                    "--type",
                    "market",
                    "--no-confirm",
                ],
            )

        assert result.exit_code == 0
        mock.place_order.assert_awaited_once()

    def test_trade_invalid_side(self, runner: CliRunner) -> None:
        """Reject invalid side parameter."""
        result = runner.invoke(
            app,
            [
                "trade",
                "--condition-id",
                _CONDITION_ID,
                "--side",
                "hold",
                "--outcome",
                "yes",
                "--amount",
                "50",
                "--price",
                "0.65",
            ],
        )
        assert result.exit_code == 1
        assert "Side must be" in result.output

    def test_trade_invalid_outcome(self, runner: CliRunner) -> None:
        """Reject invalid outcome parameter."""
        result = runner.invoke(
            app,
            [
                "trade",
                "--condition-id",
                _CONDITION_ID,
                "--side",
                "buy",
                "--outcome",
                "maybe",
                "--amount",
                "50",
                "--price",
                "0.65",
            ],
        )
        assert result.exit_code == 1
        assert "Outcome must be" in result.output

    def test_trade_invalid_price(self, runner: CliRunner) -> None:
        """Reject limit price outside valid range."""
        result = runner.invoke(
            app,
            [
                "trade",
                "--condition-id",
                _CONDITION_ID,
                "--side",
                "buy",
                "--outcome",
                "yes",
                "--amount",
                "50",
                "--price",
                "1.50",
                "--type",
                "limit",
            ],
        )
        assert result.exit_code == 1
        assert "price must be between" in result.output.lower()

    def test_trade_negative_amount(self, runner: CliRunner) -> None:
        """Reject non-positive amount."""
        result = runner.invoke(
            app,
            [
                "trade",
                "--condition-id",
                _CONDITION_ID,
                "--side",
                "buy",
                "--outcome",
                "yes",
                "--amount",
                "-10",
                "--price",
                "0.65",
            ],
        )
        assert result.exit_code == 1
        assert "positive" in result.output.lower()

    def test_trade_missing_private_key(self, runner: CliRunner) -> None:
        """Abort when POLYMARKET_PRIVATE_KEY is not set."""
        with patch.dict("os.environ", {"POLYMARKET_PRIVATE_KEY": ""}, clear=False):
            result = runner.invoke(
                app,
                [
                    "trade",
                    "--condition-id",
                    _CONDITION_ID,
                    "--side",
                    "buy",
                    "--outcome",
                    "yes",
                    "--amount",
                    "50",
                    "--price",
                    "0.65",
                    "--no-confirm",
                ],
            )
        assert result.exit_code == 1
        assert "POLYMARKET_PRIVATE_KEY" in result.output

    def test_trade_api_error(self, runner: CliRunner) -> None:
        """Handle API errors gracefully during trade."""
        mock = _mock_client()
        mock.place_order = AsyncMock(
            side_effect=PolymarketAPIError(msg="Insufficient funds", status_code=400),
        )
        with patch(
            "trading_tools.apps.polymarket.cli.trade_cmd.build_authenticated_client",
            return_value=mock,
        ):
            result = runner.invoke(
                app,
                [
                    "trade",
                    "--condition-id",
                    _CONDITION_ID,
                    "--side",
                    "buy",
                    "--outcome",
                    "yes",
                    "--amount",
                    "50",
                    "--price",
                    "0.65",
                    "--no-confirm",
                ],
            )
        assert result.exit_code == 1
        assert "Insufficient funds" in result.output


class TestBalanceCommand:
    """Test suite for the balance CLI command."""

    def test_balance_displays_usdc(self, runner: CliRunner) -> None:
        """Display USDC balance and allowance."""
        mock = _mock_client()
        with patch(
            "trading_tools.apps.polymarket.cli.trade_cmd.build_authenticated_client",
            return_value=mock,
        ):
            result = runner.invoke(app, ["balance"])

        assert result.exit_code == 0
        assert "1000" in result.output
        assert "500" in result.output

    def test_balance_api_error(self, runner: CliRunner) -> None:
        """Handle API errors during balance check."""
        mock = _mock_client()
        mock.get_balance = AsyncMock(
            side_effect=PolymarketAPIError(msg="Auth failed", status_code=401),
        )
        with patch(
            "trading_tools.apps.polymarket.cli.trade_cmd.build_authenticated_client",
            return_value=mock,
        ):
            result = runner.invoke(app, ["balance"])

        assert result.exit_code == 1
        assert "Auth failed" in result.output


class TestOrdersCommand:
    """Test suite for the orders CLI command."""

    def test_orders_displays_open_orders(self, runner: CliRunner) -> None:
        """Display open orders in a table."""
        open_orders = [
            _make_order_response(order_id="order_1", status="live"),
            _make_order_response(order_id="order_2", status="live"),
        ]
        mock = _mock_client(open_orders=open_orders)
        with patch(
            "trading_tools.apps.polymarket.cli.trade_cmd.build_authenticated_client",
            return_value=mock,
        ):
            result = runner.invoke(app, ["orders"])

        assert result.exit_code == 0
        assert f"Open Orders ({_EXPECTED_OPEN_ORDERS})" in result.output
        assert "order_1" in result.output
        assert "order_2" in result.output

    def test_orders_empty(self, runner: CliRunner) -> None:
        """Display message when no open orders exist."""
        mock = _mock_client(open_orders=[])
        with patch(
            "trading_tools.apps.polymarket.cli.trade_cmd.build_authenticated_client",
            return_value=mock,
        ):
            result = runner.invoke(app, ["orders"])

        assert result.exit_code == 0
        assert "No open orders" in result.output


class TestCancelCommand:
    """Test suite for the cancel CLI command."""

    def test_cancel_order(self, runner: CliRunner) -> None:
        """Cancel an order and display result."""
        mock = _mock_client(cancel_result={"status": "cancelled"})
        with patch(
            "trading_tools.apps.polymarket.cli.trade_cmd.build_authenticated_client",
            return_value=mock,
        ):
            result = runner.invoke(app, ["cancel", "--order-id", _ORDER_ID])

        assert result.exit_code == 0
        assert "cancelled" in result.output.lower()
        mock.cancel_order.assert_awaited_once_with(_ORDER_ID)

    def test_cancel_api_error(self, runner: CliRunner) -> None:
        """Handle API errors during cancellation."""
        mock = _mock_client()
        mock.cancel_order = AsyncMock(
            side_effect=PolymarketAPIError(msg="Order not found", status_code=404),
        )
        with patch(
            "trading_tools.apps.polymarket.cli.trade_cmd.build_authenticated_client",
            return_value=mock,
        ):
            result = runner.invoke(app, ["cancel", "--order-id", "bad_id"])

        assert result.exit_code == 1
        assert "Order not found" in result.output
