"""Real-time whale signal client with WebSocket-triggered refresh.

Subscribe to Polymarket WebSocket trade events for all tracked markets.
When a trade arrives on a subscribed asset, immediately fetch whale
trades from the Data API to check if it was a whale.  This hybrid
approach gives sub-second reaction time to whale activity — the WS
provides the trigger and the REST API provides the whale identity.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import httpx

from trading_tools.apps.tick_collector.ws_client import MarketFeed
from trading_tools.core.models import ZERO

if TYPE_CHECKING:
    from collections.abc import Set

logger = logging.getLogger(__name__)

_DATA_API_BASE = "https://data-api.polymarket.com"
_TRADE_LIMIT = 100
_CACHE_TTL_SECONDS = 2


def _default_client() -> httpx.AsyncClient:
    """Create a shared httpx client with connection pooling."""
    return httpx.AsyncClient(base_url=_DATA_API_BASE, timeout=10.0)


def _empty_cache() -> dict[str, list[dict[str, Any]]]:
    """Return an empty trade cache for dataclass default_factory."""
    return {}


def _empty_set() -> set[str]:
    """Return an empty set for dataclass default_factory."""
    return set()


def _empty_asset_list() -> list[str]:
    """Return an empty list for dataclass default_factory."""
    return []


@dataclass
class WhaleSignalClient:
    """Hybrid WebSocket + REST client for real-time whale trade signals.

    A background WebSocket task subscribes to all tracked market asset
    IDs.  When any trade arrives, the asset ID is added to a
    ``_dirty_assets`` set.  The ``get_volumes`` method checks this set
    and, if any tracked market had activity, immediately refreshes
    whale trades from the Data API.

    This cuts latency from 5-second polling to sub-second: the WS
    fires instantly, the REST fetch takes ~200ms.

    Attributes:
        whale_addresses: Tuple of tracked whale wallet addresses.

    """

    whale_addresses: tuple[str, ...]
    _client: httpx.AsyncClient = field(default_factory=_default_client, repr=False)
    _trade_cache: dict[str, list[dict[str, Any]]] = field(default_factory=_empty_cache, repr=False)
    _cache_time: float = field(default=0.0, repr=False)
    _dirty_assets: set[str] = field(default_factory=_empty_set, repr=False)
    _feed: MarketFeed | None = field(default=None, repr=False)
    _ws_task: asyncio.Task[None] | None = field(default=None, repr=False)
    _subscribed_assets: list[str] = field(default_factory=_empty_asset_list, repr=False)

    async def start_ws(self, asset_ids: list[str]) -> None:
        """Start the background WebSocket listener for trade events.

        Subscribe to the given asset IDs and listen for
        ``last_trade_price`` events.  When a trade arrives, mark the
        asset as dirty so the next ``get_volumes`` call triggers an
        immediate Data API refresh.

        Args:
            asset_ids: CLOB token identifiers to subscribe to.

        """
        self._subscribed_assets = list(asset_ids)
        self._feed = MarketFeed(reconnect_base_delay=2.0)
        self._ws_task = asyncio.create_task(self._ws_listener())
        logger.info("WebSocket listener started for %d assets", len(asset_ids))

    async def update_subscription(self, asset_ids: list[str]) -> None:
        """Update the WebSocket subscription with new asset IDs.

        Args:
            asset_ids: Full list of CLOB token identifiers.

        """
        self._subscribed_assets = list(asset_ids)
        if self._feed is not None:
            await self._feed.update_subscription(asset_ids)

    async def _ws_listener(self) -> None:
        """Background task: consume WS events and mark dirty assets."""
        if self._feed is None:
            return
        try:
            async for event in self._feed.stream(self._subscribed_assets):
                asset_id = event.get("asset_id", "")
                if asset_id:
                    self._dirty_assets.add(asset_id)
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("WebSocket listener crashed")

    async def refresh(self, *, force: bool = False) -> None:
        """Fetch recent trades for all whale addresses and cache them.

        Refresh when any of these conditions are true:
        - ``force`` is ``True``
        - Dirty assets exist (a trade came in via WS)
        - Cache TTL has expired (fallback for missed WS events)

        Args:
            force: Skip all cache checks and refresh immediately.

        """
        now = time.monotonic()
        has_dirty = bool(self._dirty_assets)
        cache_expired = (now - self._cache_time) >= _CACHE_TTL_SECONDS

        if not force and not has_dirty and not cache_expired:
            return
        if not force and not has_dirty and self._trade_cache:
            return

        if has_dirty:
            self._dirty_assets.clear()

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

        If dirty assets exist or cache is stale, refresh first.

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

    @property
    def has_activity(self) -> bool:
        """Return ``True`` if the WebSocket received trade events since last refresh."""
        return bool(self._dirty_assets)

    def get_dirty_assets(self) -> Set[str]:
        """Return the set of asset IDs with recent WS trade events."""
        return frozenset(self._dirty_assets)

    async def close(self) -> None:
        """Close the WebSocket, httpx client, and clear the cache."""
        if self._ws_task is not None:
            self._ws_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._ws_task
        if self._feed is not None:
            await self._feed.close()
        self._trade_cache.clear()
        await self._client.aclose()
