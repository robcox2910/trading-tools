"""Tests for the OrderExecutor shared service."""

from __future__ import annotations

import logging
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from trading_tools.apps.bot_framework.order_executor import OrderExecutor
from trading_tools.clients.polymarket.exceptions import PolymarketAPIError
from trading_tools.clients.polymarket.models import OrderResponse

_TOKEN_ID = "tok_yes_123"
_ORDER_ID = "order_abc"
_PRICE = Decimal("0.60")
_QUANTITY = Decimal(10)


def _make_order_response(order_id: str = _ORDER_ID) -> OrderResponse:
    """Create a test OrderResponse.

    Args:
        order_id: Order ID to return.

    Returns:
        OrderResponse with standard test data.

    """
    return OrderResponse(
        order_id=order_id,
        status="matched",
        token_id=_TOKEN_ID,
        side="BUY",
        price=_PRICE,
        size=_QUANTITY,
        filled=_QUANTITY,
    )


class TestOrderExecutorMarketOrders:
    """Tests for market (FOK) order placement."""

    @pytest.mark.asyncio
    async def test_market_order_returns_response(self) -> None:
        """Return the full OrderResponse on successful market order placement."""
        client = AsyncMock()
        client.place_order = AsyncMock(return_value=_make_order_response())
        executor = OrderExecutor(client=client, use_market_orders=True)

        result = await executor.place_order(_TOKEN_ID, "BUY", _PRICE, _QUANTITY)

        assert result is not None
        assert result.order_id == _ORDER_ID
        request = client.place_order.call_args[0][0]
        assert request.order_type == "market"
        assert request.side == "BUY"
        assert request.price == _PRICE
        assert request.size == _QUANTITY

    @pytest.mark.asyncio
    async def test_market_order_builds_fok_request(self) -> None:
        """Build a FOK market order request."""
        client = AsyncMock()
        client.place_order = AsyncMock(return_value=_make_order_response())
        executor = OrderExecutor(client=client, use_market_orders=True)

        await executor.place_order(_TOKEN_ID, "BUY", _PRICE, _QUANTITY)

        request = client.place_order.call_args[0][0]
        assert request.order_type == "market"
        assert request.token_id == _TOKEN_ID


class TestOrderExecutorLimitOrders:
    """Tests for limit (GTC) order placement."""

    @pytest.mark.asyncio
    async def test_limit_order_builds_gtc_request(self) -> None:
        """Build a GTC limit order request."""
        client = AsyncMock()
        client.place_order = AsyncMock(return_value=_make_order_response())
        executor = OrderExecutor(client=client, use_market_orders=False)

        result = await executor.place_order(_TOKEN_ID, "BUY", _PRICE, _QUANTITY)

        assert result is not None
        assert result.order_id == _ORDER_ID
        request = client.place_order.call_args[0][0]
        assert request.order_type == "limit"

    @pytest.mark.asyncio
    async def test_sell_side_order(self) -> None:
        """Place a SELL order correctly."""
        client = AsyncMock()
        client.place_order = AsyncMock(return_value=_make_order_response())
        executor = OrderExecutor(client=client)

        await executor.place_order(_TOKEN_ID, "SELL", _PRICE, _QUANTITY)

        request = client.place_order.call_args[0][0]
        assert request.side == "SELL"


class TestOrderExecutorErrorHandling:
    """Tests for error handling during order placement."""

    @pytest.mark.asyncio
    async def test_api_error_returns_none(self, caplog: pytest.LogCaptureFixture) -> None:
        """Return None and log the error on API failure."""
        client = AsyncMock()
        client.place_order = AsyncMock(
            side_effect=PolymarketAPIError(msg="Insufficient funds", status_code=400)
        )
        executor = OrderExecutor(client=client)

        with caplog.at_level(
            logging.ERROR,
            logger="trading_tools.apps.bot_framework.order_executor",
        ):
            result = await executor.place_order(_TOKEN_ID, "BUY", _PRICE, _QUANTITY)

        assert result is None
        assert any("Order failed" in msg for msg in caplog.messages)

    @pytest.mark.asyncio
    async def test_generic_exception_returns_none(self) -> None:
        """Return None on unexpected exceptions."""
        client = AsyncMock()
        client.place_order = AsyncMock(side_effect=ValueError("Network timeout"))
        executor = OrderExecutor(client=client)

        result = await executor.place_order(_TOKEN_ID, "BUY", _PRICE, _QUANTITY)

        assert result is None
