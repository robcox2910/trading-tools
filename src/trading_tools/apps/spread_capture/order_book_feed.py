"""WebSocket order book feed for real-time Polymarket order book data.

Maintain a local cache of order books by subscribing to the Polymarket
WebSocket ``book`` channel.  Provide synchronous reads for the trading
engine so it never needs to poll the REST API for order book data.

The feed manages one or more WebSocket connections (up to 10 instruments
per connection) and automatically reconnects with exponential backoff.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from decimal import Decimal, InvalidOperation
from typing import Any, cast

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from trading_tools.clients.polymarket.models import OrderBook, OrderLevel
from trading_tools.core.models import ZERO

logger = logging.getLogger(__name__)

_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
_MAX_ASSETS_PER_CONNECTION = 65
_RECONNECT_MAX_DELAY = 60.0
_PING_INTERVAL = 20
_PING_TIMEOUT = 10
_DEFAULT_STALE_SECONDS = 30.0


def _safe_decimal(value: Any) -> Decimal:
    """Convert a value to Decimal, returning ZERO on failure.

    Args:
        value: String or numeric value to convert.

    Returns:
        Decimal representation, or ``ZERO`` if conversion fails.

    """
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return ZERO


def parse_book_event(event: dict[str, Any]) -> tuple[str, OrderBook] | None:
    """Parse a WebSocket ``book`` event into an ``OrderBook``.

    The Polymarket WebSocket book channel sends messages with ``bids`` and
    ``asks`` arrays, each containing ``{price, size}`` objects.

    Args:
        event: Parsed event dictionary from the WebSocket.

    Returns:
        Tuple of ``(asset_id, OrderBook)`` if the event is a valid book
        update, or ``None`` if it cannot be parsed.

    """
    if event.get("event_type") != "book":
        return None

    asset_id = event.get("asset_id", "")
    if not asset_id:
        return None

    raw_bids: list[Any] = event.get("bids", [])
    raw_asks: list[Any] = event.get("asks", [])

    bids = tuple(
        OrderLevel(price=_safe_decimal(b["price"]), size=_safe_decimal(b["size"]))
        for b in raw_bids
        if isinstance(b, dict) and "price" in b and "size" in b
    )
    asks = tuple(
        OrderLevel(price=_safe_decimal(a["price"]), size=_safe_decimal(a["size"]))
        for a in raw_asks
        if isinstance(a, dict) and "price" in a and "size" in a
    )

    best_bid = bids[0].price if bids else ZERO
    best_ask = asks[0].price if asks else ZERO
    spread = best_ask - best_bid if best_bid > ZERO and best_ask > ZERO else ZERO
    midpoint = (best_bid + best_ask) / 2 if best_bid > ZERO and best_ask > ZERO else ZERO

    book = OrderBook(
        token_id=asset_id,
        bids=bids,
        asks=asks,
        spread=spread,
        midpoint=midpoint,
    )
    return asset_id, book


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


class OrderBookFeed:
    """Real-time order book cache backed by WebSocket subscriptions.

    Subscribe to the Polymarket WebSocket ``book`` channel for a set of
    token IDs and maintain a local ``OrderBook`` cache that the trading
    engine reads synchronously.  Automatically reconnect on connection
    failures with exponential backoff.

    Args:
        stale_seconds: Maximum age in seconds before a cached book is
            considered stale.

    """

    def __init__(self, *, stale_seconds: float = _DEFAULT_STALE_SECONDS) -> None:
        """Initialize the order book feed.

        Args:
            stale_seconds: Maximum age before a cached book is stale.

        """
        self._stale_seconds = stale_seconds
        self._books: dict[str, OrderBook] = {}
        self._last_update: dict[str, float] = {}
        self._subscribed_tokens: list[str] = []
        self._ws: ClientConnection | None = None
        self._task: asyncio.Task[None] | None = None
        self._closed = False
        self._reconnect_requested = False
        self._event_count = 0

    async def start(self, token_ids: list[str]) -> None:
        """Start the WebSocket connection and subscribe to token IDs.

        Launch a background asyncio task that maintains the connection
        and updates the book cache.

        Args:
            token_ids: Initial token IDs to subscribe to.

        """
        self._subscribed_tokens = list(token_ids)
        self._closed = False
        self._task = asyncio.create_task(self._run_loop())
        logger.info("OrderBookFeed started for %d tokens", len(token_ids))

    async def stop(self) -> None:
        """Gracefully close the WebSocket connection and cancel the task."""
        self._closed = True
        await self._close_ws()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None
        logger.info(
            "OrderBookFeed stopped (processed %d events, %d tokens cached)",
            self._event_count,
            len(self._books),
        )

    async def update_subscription(self, token_ids: list[str]) -> None:
        """Update the subscription to a new set of token IDs.

        Force a WebSocket reconnect to apply the new subscription since
        the Polymarket server ignores subsequent subscribe messages on
        an existing connection.

        Args:
            token_ids: Full list of token IDs to subscribe to.

        """
        old_count = len(self._subscribed_tokens)
        self._subscribed_tokens = list(token_ids)
        if self._ws is not None:
            self._reconnect_requested = True
            await self._close_ws()
        logger.info(
            "OrderBookFeed subscription updated: %d -> %d tokens",
            old_count,
            len(token_ids),
        )

    async def _close_ws(self) -> None:
        """Close the WebSocket connection, ignoring errors."""
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.close()
            self._ws = None

    def get_book(self, token_id: str) -> OrderBook | None:
        """Return the cached order book for a token, or None if unavailable.

        Args:
            token_id: CLOB token identifier.

        Returns:
            Cached ``OrderBook`` or ``None`` if not subscribed or no data
            has arrived yet.

        """
        return self._books.get(token_id)

    def is_stale(self, token_id: str) -> bool:
        """Check whether the cached book for a token is too old.

        Args:
            token_id: CLOB token identifier.

        Returns:
            ``True`` if the book is missing or older than ``stale_seconds``.

        """
        last = self._last_update.get(token_id)
        if last is None:
            return True
        return (time.monotonic() - last) > self._stale_seconds

    @property
    def subscribed_tokens(self) -> list[str]:
        """Return the currently subscribed token ID list."""
        return list(self._subscribed_tokens)

    @property
    def event_count(self) -> int:
        """Return the total number of book events processed."""
        return self._event_count

    @property
    def cached_token_count(self) -> int:
        """Return the number of tokens with cached order books."""
        return len(self._books)

    async def _run_loop(self) -> None:
        """Maintain the WebSocket connection with auto-reconnect."""
        delay = 5.0
        consecutive_failures = 0
        while not self._closed:
            try:
                await self._connect_and_listen()
                delay = 5.0
                consecutive_failures = 0
            except ConnectionClosed:
                if self._closed:
                    return
                if not self._reconnect_requested:
                    consecutive_failures += 1
                    logger.warning(
                        "OrderBookFeed connection closed (failures=%d)",
                        consecutive_failures,
                    )
            except OSError as exc:
                if self._closed:
                    return
                consecutive_failures += 1
                logger.warning(
                    "OrderBookFeed connection error: %s (failures=%d)",
                    exc,
                    consecutive_failures,
                )
            except Exception:
                if self._closed:
                    return
                consecutive_failures += 1
                logger.exception(
                    "OrderBookFeed unexpected error (failures=%d)",
                    consecutive_failures,
                )

            if self._closed:
                return

            if self._reconnect_requested:
                self._reconnect_requested = False
                delay = 5.0
                consecutive_failures = 0
                logger.info("OrderBookFeed reconnecting for subscription update")
                continue

            logger.info("OrderBookFeed reconnecting in %.1fs...", delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, _RECONNECT_MAX_DELAY)

    async def _connect_and_listen(self) -> None:
        """Open a connection, subscribe, and process book events."""
        if not self._subscribed_tokens:
            await asyncio.sleep(5.0)
            return

        async with connect(
            _WS_URL,
            ping_interval=_PING_INTERVAL,
            ping_timeout=_PING_TIMEOUT,
        ) as ws:
            self._ws = ws
            msg = _build_subscribe_message(self._subscribed_tokens)
            try:
                await ws.send(json.dumps(msg))
            except (ConnectionClosed, AttributeError):
                logger.warning("OrderBookFeed: connection closed before subscribe")
                return
            logger.info(
                "OrderBookFeed connected and subscribed to %d tokens",
                len(self._subscribed_tokens),
            )

            async for raw in ws:
                self._process_message(raw)

    def _process_message(self, raw: str | bytes) -> None:
        """Parse a raw WS message and update the book cache.

        Args:
            raw: Raw WebSocket message.

        """
        try:
            data: Any = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return

        events: list[dict[str, Any]] = []
        if isinstance(data, dict):
            events = [data]
        elif isinstance(data, list):
            events = [
                cast("dict[str, Any]", item)
                for item in cast("list[Any]", data)
                if isinstance(item, dict)
            ]

        now = time.monotonic()
        for event in events:
            result = parse_book_event(event)
            if result is not None:
                asset_id, book = result
                self._books[asset_id] = book
                self._last_update[asset_id] = now
                self._event_count += 1
