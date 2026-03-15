"""USDC balance management service for live trading bots.

Fetch and cache the live USDC balance from the Polymarket CLOB API.
Both the snipe trading bot and the whale copy-trader compose this
service to avoid duplicating balance refresh logic.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from trading_tools.core.models import ZERO

if TYPE_CHECKING:
    from decimal import Decimal

    from trading_tools.clients.polymarket.client import PolymarketClient

logger = logging.getLogger(__name__)


@dataclass
class BalanceManager:
    """Fetch and cache USDC balance from the Polymarket CLOB API.

    Call ``sync_balance`` to flush the CLOB's cached on-chain state,
    then ``get_balance`` to read the current USDC balance. Optionally
    fetch the full portfolio value (USDC + open positions). On transient
    API failures, log a warning and return the last known values so the
    bot can continue operating.

    Attributes:
        client: Authenticated Polymarket client with balance endpoints.

    """

    client: PolymarketClient
    _balance: Decimal = field(default=ZERO, init=False, repr=False)
    _portfolio_value: Decimal = field(default=ZERO, init=False, repr=False)

    async def refresh(self, *, include_portfolio: bool = False) -> Decimal:
        """Refresh the live USDC balance from the CLOB API.

        Call ``sync_balance`` first to ensure the cached value reflects
        the latest on-chain state, then read the balance. Optionally
        refresh the full portfolio value as well.

        Args:
            include_portfolio: Also fetch the total portfolio value
                (USDC + all open position market values).

        Returns:
            Current USDC balance as a ``Decimal``, or the last known
            balance if the API call fails.

        """
        try:
            await self.client.sync_balance("COLLATERAL")
            bal = await self.client.get_balance("COLLATERAL")
            self._balance = bal.balance
        except Exception:
            logger.warning(
                "Balance refresh failed, using last known: $%.4f",
                self._balance,
                exc_info=True,
            )

        if include_portfolio:
            try:
                self._portfolio_value = await self.client.get_portfolio_value()
            except Exception:
                logger.warning(
                    "Portfolio value refresh failed, using last known: $%.4f",
                    self._portfolio_value,
                    exc_info=True,
                )

        logger.info("BALANCE refreshed: $%.2f", self._balance)
        return self._balance

    @property
    def balance(self) -> Decimal:
        """Return the last-fetched USDC balance."""
        return self._balance

    @property
    def portfolio_value(self) -> Decimal:
        """Return the last-fetched total portfolio value (USDC + positions)."""
        return self._portfolio_value
