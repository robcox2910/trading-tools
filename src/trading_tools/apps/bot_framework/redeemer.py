"""CTF position redemption service for resolved prediction markets.

Discover redeemable winning positions via the Polymarket Data API and
execute on-chain CTF redemption in the background so the trading loop is
not blocked by slow Polygon transactions.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trading_tools.clients.polymarket.client import PolymarketClient

logger = logging.getLogger(__name__)

_DEFAULT_MIN_ORDER_SIZE = Decimal(5)


@dataclass
class PositionRedeemer:
    """Discover and redeem resolved winning positions on-chain.

    Query the Polymarket Data API for redeemable positions held by the
    proxy wallet, filter out positions below the minimum order size, and
    spawn a background asyncio task to call ``redeem_positions()`` on the
    CTF contract.  If a previous redemption task is still running it is
    cancelled before starting a new one.

    Attributes:
        client: Authenticated Polymarket client with redemption capability.
        min_order_size: Minimum token size to consider for redemption.

    """

    client: PolymarketClient
    min_order_size: Decimal = _DEFAULT_MIN_ORDER_SIZE
    _task: asyncio.Task[None] | None = field(default=None, repr=False)

    async def redeem_if_available(self) -> None:
        """Discover redeemable positions and spawn background CTF redemption.

        Cancel any in-flight redemption task before starting a new one.
        Positions below ``min_order_size`` are logged and skipped.
        Discovery errors are caught and logged without propagation.
        """
        if self._task is not None and not self._task.done():
            logger.info("AUTO-REDEEM: cancelling previous redemption task")
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

        try:
            redeemable = await self.client.get_redeemable_positions()
        except Exception:
            logger.warning("Failed to discover redeemable positions", exc_info=True)
            return

        if not redeemable:
            return

        logger.info("AUTO-REDEEM: found %d redeemable positions", len(redeemable))
        condition_ids: list[str] = []
        for pos in redeemable:
            if pos.size < self.min_order_size:
                logger.info(
                    "REDEEM skip %s: size=%s below minimum %s",
                    pos.title[:40],
                    pos.size,
                    self.min_order_size,
                )
                continue
            condition_ids.append(pos.condition_id)

        if not condition_ids:
            return

        self._task = asyncio.create_task(self._redeem_on_chain(condition_ids))

    async def _redeem_on_chain(self, condition_ids: list[str]) -> None:
        """Execute on-chain CTF redemption in the background.

        Log results when complete.  Errors are caught and logged so they
        do not propagate to the main trading loop.

        Args:
            condition_ids: Resolved market condition IDs to redeem.

        """
        try:
            redeemed = await self.client.redeem_positions(condition_ids)
            logger.info(
                "AUTO-REDEEM: redeemed %d/%d positions on-chain via CTF",
                redeemed,
                len(condition_ids),
            )
        except Exception:
            logger.warning("CTF redemption failed", exc_info=True)

    @property
    def task(self) -> asyncio.Task[None] | None:
        """Return the current background redemption task, if any."""
        return self._task
