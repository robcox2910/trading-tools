"""Async WebSocket client for the Polymarket CLOB market channel.

Connect to the Polymarket WebSocket endpoint, subscribe to trade events for
a set of asset IDs, and yield parsed ``last_trade_price`` messages as they
arrive. Handle auto-reconnect with exponential backoff, ping/pong keepalive,
and graceful shutdown.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any, cast

from websockets import ConnectionClosed
from websockets.asyncio.client import ClientConnection, connect

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
_RECONNECT_MAX_DELAY = 60.0
_PING_INTERVAL = 20
_PING_TIMEOUT = 10


class MarketFeed:
    """Async WebSocket client for streaming Polymarket trade events.

    Connect to the CLOB WebSocket market channel, subscribe to one or more
    asset IDs, and yield ``last_trade_price`` event payloads. Automatically
    reconnect with exponential backoff on connection failures.

    Args:
        reconnect_base_delay: Initial reconnect wait in seconds, doubled on
            each consecutive failure up to 60 seconds.

    """

    def __init__(self, *, reconnect_base_delay: float = 5.0) -> None:
        """Initialize the market feed.

        Args:
            reconnect_base_delay: Initial delay in seconds between reconnect
                attempts.

        """
        self._reconnect_base_delay = reconnect_base_delay
        self._ws: ClientConnection | None = None
        self._closed = False
        self._reconnect_requested = False

    async def stream(self, asset_ids: list[str]) -> AsyncIterator[dict[str, Any]]:
        """Connect and yield ``last_trade_price`` events indefinitely.

        Automatically reconnect on failures with exponential backoff. Only
        events of type ``last_trade_price`` are yielded; all other message
        types (``book``, ``price_change``, etc.) are silently discarded.

        When ``update_subscription()`` triggers an intentional reconnect, the
        backoff delay is skipped so the fresh connection is established
        immediately.

        Args:
            asset_ids: Token identifiers to subscribe to.

        Yields:
            Parsed event dictionaries containing trade data.

        """
        delay = self._reconnect_base_delay
        while not self._closed:
            try:
                async for event in self._connect_and_listen(asset_ids):
                    yield event
                    delay = self._reconnect_base_delay
            except ConnectionClosed as exc:
                if self._closed:
                    return
                if not self._reconnect_requested:
                    logger.warning("WebSocket connection closed: %s", exc)
            except OSError as exc:
                if self._closed:
                    return
                logger.warning("WebSocket connection error: %s", exc)

            if self._closed:
                return

            if self._reconnect_requested:
                self._reconnect_requested = False
                delay = self._reconnect_base_delay
                logger.info("Reconnecting immediately for subscription update")
                continue

            logger.info("Reconnecting in %.1fs...", delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, _RECONNECT_MAX_DELAY)

    async def update_subscription(self, asset_ids: list[str]) -> None:
        """Force a WebSocket reconnect to apply a new subscription.

        The Polymarket server silently ignores subsequent subscribe messages
        on an existing connection. Close the current WebSocket so the
        ``stream()`` reconnect loop re-establishes a fresh connection with
        the updated asset IDs.

        Args:
            asset_ids: Full list of token identifiers (used for logging only;
                the reconnect will read the caller's updated list directly).

        """
        if self._ws is not None:
            self._reconnect_requested = True
            logger.info(
                "Closing WebSocket for subscription update (%d assets)",
                len(asset_ids),
            )
            await self._ws.close()

    async def close(self) -> None:
        """Gracefully close the WebSocket connection."""
        self._closed = True
        if self._ws is not None:
            await self._ws.close()
            self._ws = None
        logger.info("MarketFeed closed")

    async def _connect_and_listen(self, asset_ids: list[str]) -> AsyncIterator[dict[str, Any]]:
        """Open a connection, subscribe, and yield trade events.

        Args:
            asset_ids: Token identifiers to subscribe to.

        Yields:
            Parsed ``last_trade_price`` event dictionaries.

        """
        async with connect(
            _WS_URL,
            ping_interval=_PING_INTERVAL,
            ping_timeout=_PING_TIMEOUT,
        ) as ws:
            self._ws = ws
            subscribe_msg = _build_subscribe_message(asset_ids)
            await ws.send(json.dumps(subscribe_msg))
            logger.info("Connected and subscribed to %d assets", len(asset_ids))

            async for raw in ws:
                events = _parse_message(raw)
                for event in events:
                    yield event


def _build_subscribe_message(asset_ids: list[str]) -> dict[str, object]:
    """Build a WebSocket subscription message for the market channel.

    Args:
        asset_ids: Token identifiers to subscribe to.

    Returns:
        Subscription message dictionary.

    """
    return {
        "type": "market",
        "assets_ids": asset_ids,
        "custom_feature_enabled": False,
    }


def _parse_message(raw: str | bytes) -> list[dict[str, Any]]:
    """Parse a raw WebSocket message and extract trade events.

    Filter for ``last_trade_price`` event types only. Return an empty
    list for non-trade messages or malformed payloads.

    Args:
        raw: Raw WebSocket message (string or bytes).

    Returns:
        List of parsed ``last_trade_price`` event dictionaries. Empty if
        the message is not a trade event or cannot be parsed.

    """
    try:
        data: Any = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.debug("Ignoring unparseable message: %s", raw[:100] if raw else raw)
        return []

    # Single event
    if isinstance(data, dict):
        event = cast("dict[str, Any]", data)
        return [event] if _is_trade_event(event) else []

    # Array of events
    if isinstance(data, list):
        items = cast("list[Any]", data)
        return [
            cast("dict[str, Any]", item)
            for item in items
            if isinstance(item, dict) and _is_trade_event(cast("dict[str, Any]", item))
        ]

    return []


def _is_trade_event(event: dict[str, Any]) -> bool:
    """Check whether an event is a ``last_trade_price`` type.

    Args:
        event: Parsed event dictionary.

    Returns:
        True if the event type is ``last_trade_price``.

    """
    return event.get("event_type") == "last_trade_price"
