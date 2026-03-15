"""CLOB order placement wrapper with error handling.

Provide a simple, reusable service for placing orders on the Polymarket
CLOB API.  Handle ``OrderRequest`` construction (FOK market vs GTC limit),
call ``client.place_order()``, and catch API errors gracefully.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from trading_tools.clients.polymarket.exceptions import PolymarketError
from trading_tools.clients.polymarket.models import OrderRequest, OrderResponse

if TYPE_CHECKING:
    from decimal import Decimal

    from trading_tools.clients.polymarket.client import PolymarketClient

logger = logging.getLogger(__name__)


@dataclass
class OrderExecutor:
    """Place CLOB orders with automatic request construction and error handling.

    Construct ``OrderRequest`` objects with the correct order type (FOK
    market or GTC limit) based on configuration, submit them via the
    authenticated Polymarket client, and return the full response on
    success or ``None`` on failure.

    Attributes:
        client: Authenticated Polymarket client for order placement.
        use_market_orders: Use FOK market orders when ``True``, GTC limit
            orders when ``False``.

    """

    client: PolymarketClient
    use_market_orders: bool = True

    async def place_order(
        self,
        token_id: str,
        side: str,
        price: Decimal,
        quantity: Decimal,
        order_type: str | None = None,
    ) -> OrderResponse | None:
        """Place a CLOB order and return the full response.

        Build an ``OrderRequest`` with the configured order type, submit it
        via the client, and return the ``OrderResponse`` on success.  On API
        failure, log the error and return ``None`` so the caller can continue.

        Args:
            token_id: CLOB token identifier for the outcome to trade.
            side: Order side -- ``"BUY"`` or ``"SELL"``.
            price: Limit price between 0 and 1.
            quantity: Number of tokens to trade.
            order_type: Explicit order type override (``"market"`` or
                ``"limit"``). When ``None``, falls back to the instance-level
                ``use_market_orders`` setting.

        Returns:
            ``OrderResponse`` on success, or ``None`` if the order failed.

        """
        if order_type is None:
            order_type = "market" if self.use_market_orders else "limit"
        request = OrderRequest(
            token_id=token_id,
            side=side,
            price=price,
            size=quantity,
            order_type=order_type,
        )

        try:
            return await self.client.place_order(request)
        except (PolymarketError, KeyError, ValueError):
            logger.exception("Order failed for token %s", token_id[:12])
            return None
