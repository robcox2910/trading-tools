"""Tests for the directional LiveExecution adapter."""

from decimal import Decimal
from unittest.mock import AsyncMock

import httpx
import pytest

from trading_tools.apps.bot_framework.balance_manager import BalanceManager
from trading_tools.apps.bot_framework.order_executor import OrderExecutor
from trading_tools.apps.directional.adapters import LiveExecution
from trading_tools.apps.directional.ports import FillResult
from trading_tools.clients.polymarket.models import OrderResponse
from trading_tools.core.models import ZERO

_PRICE = Decimal("0.55")
_QTY = Decimal(50)
_ORDER_ID = "ord_1"
_TOKEN_ID = "tok_abc123"
_BALANCE = Decimal("100.00")
_COMMITTED = Decimal("25.00")


def _make_order_response(
    *,
    filled: Decimal = _QTY,
    price: Decimal = _PRICE,
    order_id: str = _ORDER_ID,
) -> OrderResponse:
    """Create a test OrderResponse.

    Args:
        filled: Quantity filled.
        price: Fill price.
        order_id: CLOB order identifier.

    Returns:
        A minimal ``OrderResponse``.

    """
    return OrderResponse(
        order_id=order_id,
        status="MATCHED",
        token_id=_TOKEN_ID,
        side="BUY",
        price=price,
        size=filled,
        filled=filled,
    )


def _make_adapter(
    *,
    balance: Decimal = _BALANCE,
    committed: Decimal = ZERO,
) -> LiveExecution:
    """Build a LiveExecution with mocked client dependencies.

    Args:
        balance: Balance to report from the balance manager.
        committed: Committed capital returned by the callable.

    Returns:
        A configured ``LiveExecution`` instance.

    """
    client = AsyncMock()
    executor = OrderExecutor(client=client, use_market_orders=True)
    balance_manager = BalanceManager(client=client)
    balance_manager._balance = balance
    return LiveExecution(
        executor=executor,
        balance_manager=balance_manager,
        committed_capital_fn=lambda: committed,
    )


class TestLiveExecutionFill:
    """Test execute_fill for various CLOB responses."""

    @pytest.mark.asyncio
    async def test_execute_fill_success(self) -> None:
        """Successful fill returns FillResult with CLOB response values."""
        adapter = _make_adapter()
        resp = _make_order_response()
        adapter.executor.client.place_order = AsyncMock(return_value=resp)

        result = await adapter.execute_fill(_TOKEN_ID, "BUY", _PRICE, _QTY)

        assert result is not None
        assert result == FillResult(price=_PRICE, quantity=_QTY, order_id=_ORDER_ID)

    @pytest.mark.asyncio
    async def test_execute_fill_rejected(self) -> None:
        """Return None when executor returns None (order rejected)."""
        adapter = _make_adapter()
        adapter.executor.client.place_order = AsyncMock(return_value=None)

        result = await adapter.execute_fill(_TOKEN_ID, "BUY", _PRICE, _QTY)

        assert result is None

    @pytest.mark.asyncio
    async def test_execute_fill_zero_filled(self) -> None:
        """Return None when filled quantity is zero."""
        adapter = _make_adapter()
        resp = _make_order_response(filled=ZERO)
        adapter.executor.client.place_order = AsyncMock(return_value=resp)

        result = await adapter.execute_fill(_TOKEN_ID, "BUY", _PRICE, _QTY)

        assert result is None

    @pytest.mark.asyncio
    async def test_execute_fill_api_error(self) -> None:
        """Return None on httpx.HTTPError without propagating."""
        adapter = _make_adapter()
        adapter.executor.client.place_order = AsyncMock(
            side_effect=httpx.HTTPError("timeout"),
        )

        result = await adapter.execute_fill(_TOKEN_ID, "BUY", _PRICE, _QTY)

        assert result is None


class TestLiveExecutionCapital:
    """Test capital query methods."""

    def test_get_capital_returns_balance(self) -> None:
        """get_capital delegates to balance_manager.balance."""
        adapter = _make_adapter(balance=_BALANCE)

        assert adapter.get_capital() == _BALANCE

    def test_total_capital_includes_committed(self) -> None:
        """total_capital returns balance + committed capital."""
        adapter = _make_adapter(balance=_BALANCE, committed=_COMMITTED)

        assert adapter.total_capital() == _BALANCE + _COMMITTED
