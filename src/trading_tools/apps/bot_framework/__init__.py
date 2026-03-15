"""Shared bot framework with composable services for trading engines.

Provide reusable building blocks that both the WebSocket-driven live engine
and the polling-driven whale copy-trader compose to avoid duplicating
infrastructure code (balance management, heartbeat monitoring, graceful
shutdown, redemption, order placement).

Public API:
    - ``BalanceManager``: fetch and cache live USDC balance from the CLOB.
    - ``HeartbeatLogger``: emit periodic structured monitoring log lines.
    - ``GracefulShutdown``: coordinate clean exit on SIGINT/SIGTERM.
    - ``PositionRedeemer``: discover and redeem resolved positions on-chain.
    - ``OrderExecutor``: place CLOB orders with error handling.
"""

from trading_tools.apps.bot_framework.balance_manager import BalanceManager
from trading_tools.apps.bot_framework.heartbeat import HeartbeatLogger
from trading_tools.apps.bot_framework.order_executor import OrderExecutor
from trading_tools.apps.bot_framework.redeemer import PositionRedeemer
from trading_tools.apps.bot_framework.shutdown import GracefulShutdown

__all__ = [
    "BalanceManager",
    "GracefulShutdown",
    "HeartbeatLogger",
    "OrderExecutor",
    "PositionRedeemer",
]
