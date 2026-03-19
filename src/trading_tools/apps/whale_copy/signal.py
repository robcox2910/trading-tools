"""Real-time whale directional signal client for Polymarket.

Query the Polymarket Data API for whale BUY trades on a given market
and compute the net directional signal (which side has more dollar
volume) with a conviction ratio.

Fetch each whale's recent trades **once per refresh** and cache them
so that multiple ``get_direction`` calls within the same poll cycle
reuse the same data instead of hitting the API N times per position.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import httpx

from trading_tools.core.models import ZERO

logger = logging.getLogger(__name__)

_DATA_API_BASE = "https://data-api.polymarket.com"
_TRADE_LIMIT = 100
_CACHE_TTL_SECONDS = 4


def _default_client() -> httpx.AsyncClient:
    """Create a shared httpx client with connection pooling."""
    return httpx.AsyncClient(base_url=_DATA_API_BASE, timeout=10.0)


def _empty_cache() -> dict[str, list[dict[str, Any]]]:
    """Return an empty trade cache for dataclass default_factory."""
    return {}


@dataclass
class WhaleSignalClient:
    """Query whale trade activity to determine directional consensus.

    Fetch each whale's recent trades once per poll cycle (cached for
    ``_CACHE_TTL_SECONDS``), then filter client-side by condition ID and
    window timestamp.  This avoids redundant API calls when the trader
    queries multiple positions in the same cycle.

    Attributes:
        whale_addresses: Tuple of tracked whale wallet addresses.

    """

    whale_addresses: tuple[str, ...]
    _client: httpx.AsyncClient = field(default_factory=_default_client, repr=False)
    _trade_cache: dict[str, list[dict[str, Any]]] = field(default_factory=_empty_cache, repr=False)
    _cache_time: float = field(default=0.0, repr=False)

    async def refresh(self) -> None:
        """Fetch recent trades for all whale addresses and cache them.

        Call once per poll cycle before any ``get_direction`` calls.
        Subsequent ``get_direction`` calls will use the cached data
        until ``_CACHE_TTL_SECONDS`` elapses.
        """
        now = time.monotonic()
        if now - self._cache_time < _CACHE_TTL_SECONDS and self._trade_cache:
            return

        self._trade_cache.clear()
        for address in self.whale_addresses:
            try:
                resp = await self._client.get(
                    "/trades",
                    params={
                        "user": address,
                        "limit": _TRADE_LIMIT,
                    },
                )
                resp.raise_for_status()
                self._trade_cache[address] = resp.json()
            except (httpx.HTTPError, ValueError):
                logger.debug("Failed to fetch trades for whale %s", address[:10])
                self._trade_cache[address] = []

        self._cache_time = now

    async def get_volumes(
        self, condition_id: str, window_start_ts: int = 0
    ) -> tuple[Decimal, Decimal]:
        """Return whale BUY dollar volume on each side from cached trades.

        If the cache is stale or empty, refresh it first.  Then filter
        cached trades by condition ID, BUY side, and window timestamp.

        Args:
            condition_id: Polymarket market condition identifier.
            window_start_ts: Only count trades at or after this epoch
                second.  Defaults to 0 (no filtering).

        Returns:
            Tuple of ``(up_volume, down_volume)`` in USDC.
            Both are ``ZERO`` if no matching whale trades found.

        """
        await self.refresh()

        up_volume = ZERO
        down_volume = ZERO

        for trades in self._trade_cache.values():
            for trade in trades:
                if trade.get("side") != "BUY":
                    continue
                if trade.get("conditionId") != condition_id:
                    continue
                trade_ts = int(trade.get("timestamp", 0))
                if trade_ts < window_start_ts:
                    continue
                outcome = trade.get("outcome", "")
                size = Decimal(str(trade.get("size", 0)))
                price = Decimal(str(trade.get("price", 0)))
                dollar_volume = size * price

                if outcome == "Up":
                    up_volume += dollar_volume
                elif outcome == "Down":
                    down_volume += dollar_volume

        return up_volume, down_volume

    async def get_direction(
        self, condition_id: str, window_start_ts: int = 0
    ) -> tuple[str | None, Decimal]:
        """Return the whale consensus direction for a market.

        Convenience wrapper around ``get_volumes`` that returns the
        favoured side and conviction ratio.

        Args:
            condition_id: Polymarket market condition identifier.
            window_start_ts: Only count trades at or after this epoch
                second.  Defaults to 0 (no filtering).

        Returns:
            Tuple of ``(favoured_side, conviction_ratio)``.
            Returns ``(None, ZERO)`` if no matching whale trades found.

        """
        up_volume, down_volume = await self.get_volumes(condition_id, window_start_ts)

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
        """Close the underlying httpx client and clear the cache."""
        self._trade_cache.clear()
        await self._client.aclose()
