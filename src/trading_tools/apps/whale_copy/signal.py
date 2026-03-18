"""Real-time whale directional signal client for Polymarket.

Query the Polymarket Data API for whale BUY trades on a given market
and compute the net directional signal (which side has more dollar
volume) with a conviction ratio.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal

import httpx

from trading_tools.core.models import ZERO

logger = logging.getLogger(__name__)

_DATA_API_BASE = "https://data-api.polymarket.com"
_TRADE_LIMIT = 100


def _default_client() -> httpx.AsyncClient:
    """Create a shared httpx client with connection pooling."""
    return httpx.AsyncClient(base_url=_DATA_API_BASE, timeout=10.0)


@dataclass
class WhaleSignalClient:
    """Query whale trade activity to determine directional consensus.

    Aggregate BUY dollar volume (size * price) by outcome across all
    tracked whale addresses for a given market condition.  Return the
    favoured side and the conviction ratio (dollar volume on favoured
    side divided by the other side).

    Attributes:
        whale_addresses: Tuple of tracked whale wallet addresses.

    """

    whale_addresses: tuple[str, ...]
    _client: httpx.AsyncClient = field(default_factory=_default_client, repr=False)

    async def get_direction(self, condition_id: str) -> tuple[str | None, Decimal]:
        """Query whale BUY trades on a market and return the favoured direction.

        Fetch recent trades for each tracked whale address, filter to
        BUY trades matching the condition ID, and aggregate dollar
        volume by outcome.

        Args:
            condition_id: Polymarket market condition identifier.

        Returns:
            Tuple of ``(favoured_side, conviction_ratio)``.
            ``favoured_side`` is ``"Up"`` or ``"Down"`` based on which
            outcome has higher dollar volume.  ``conviction_ratio`` is
            the ratio of dollar volume on the favoured side to the other.
            Returns ``(None, ZERO)`` if no whale trades found.

        """
        up_volume = ZERO
        down_volume = ZERO

        for address in self.whale_addresses:
            try:
                resp = await self._client.get(
                    "/trades",
                    params={
                        "user": address,
                        "conditionId": condition_id,
                        "limit": _TRADE_LIMIT,
                    },
                )
                resp.raise_for_status()
                trades = resp.json()
            except (httpx.HTTPError, ValueError):
                logger.debug("Failed to fetch trades for whale %s", address[:10])
                continue

            for trade in trades:
                if trade.get("side") != "BUY":
                    continue
                outcome = trade.get("outcome", "")
                size = Decimal(str(trade.get("size", 0)))
                price = Decimal(str(trade.get("price", 0)))
                dollar_volume = size * price

                if outcome == "Up":
                    up_volume += dollar_volume
                elif outcome == "Down":
                    down_volume += dollar_volume

        if up_volume == ZERO and down_volume == ZERO:
            return None, ZERO

        if up_volume >= down_volume:
            favoured = "Up"
            ratio = up_volume / down_volume if down_volume > ZERO else up_volume
        else:
            favoured = "Down"
            ratio = down_volume / up_volume if up_volume > ZERO else down_volume

        return favoured, ratio

    async def close(self) -> None:
        """Close the underlying httpx client."""
        await self._client.aclose()
