"""Shared bot framework with composable services for trading engines.

Provide reusable building blocks that both the WebSocket-driven live engine
and the polling-driven whale copy-trader compose to avoid duplicating
infrastructure code (redemption, order placement).

Public API:
    - ``PositionRedeemer``: discover and redeem resolved positions on-chain.
    - ``OrderExecutor``: place CLOB orders with error handling.
"""

from trading_tools.apps.bot_framework.order_executor import OrderExecutor
from trading_tools.apps.bot_framework.redeemer import PositionRedeemer

__all__ = ["OrderExecutor", "PositionRedeemer"]
