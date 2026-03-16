"""Main orchestrator for the tick collector service.

Wire together the WebSocket market feed, tick repository, and market discovery
to capture every trade from Polymarket in real time. Optionally poll the CLOB
REST API for periodic order book depth snapshots. Handle buffered writes,
periodic market re-discovery, heartbeat logging, and graceful shutdown.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING, Any

from trading_tools.apps.bot_framework.heartbeat import HeartbeatLogger
from trading_tools.apps.bot_framework.shutdown import GracefulShutdown
from trading_tools.apps.tick_collector.models import OrderBookSnapshot, Tick
from trading_tools.apps.tick_collector.repository import TickRepository
from trading_tools.apps.tick_collector.ws_client import MarketFeed
from trading_tools.clients.polymarket.client import PolymarketClient
from trading_tools.clients.polymarket.exceptions import PolymarketError

if TYPE_CHECKING:
    from trading_tools.apps.tick_collector.config import CollectorConfig
    from trading_tools.clients.polymarket.models import OrderBook

from trading_tools.core.timestamps import FIVE_MINUTES, MS_PER_SECOND, now_ms

logger = logging.getLogger(__name__)


def _seconds_until_next_discovery(now: int, lead_seconds: int) -> int:
    """Compute seconds to sleep before the next window-aligned discovery.

    Determine how long to wait so that discovery fires ``lead_seconds``
    before the next 5-minute window boundary.  If the fire time has
    already passed within the current window, return the sleep duration
    until the *next* window's fire time so the loop doesn't busy-spin.

    Args:
        now: Current Unix epoch in seconds.
        lead_seconds: How many seconds before the boundary to fire.

    Returns:
        Non-negative seconds to sleep.

    """
    elapsed = now % FIVE_MINUTES
    fire_at = FIVE_MINUTES - lead_seconds
    remaining = fire_at - elapsed
    if remaining <= 0:
        # Past fire time — sleep until next window's fire time
        return remaining + FIVE_MINUTES
    return remaining


class TickCollector:
    """Orchestrate real-time tick collection from Polymarket WebSocket.

    Discover markets from series slugs via the Gamma API, connect to the
    WebSocket market channel, buffer incoming trade events, and flush them
    to the database in batches. Periodically re-discover markets to pick up
    new rotations and log heartbeat stats for CloudWatch monitoring.

    Args:
        config: Immutable collector configuration.

    """

    def __init__(self, config: CollectorConfig) -> None:
        """Initialize the collector with the given configuration.

        Args:
            config: Collector configuration with DB URL, markets, and
                flush parameters.

        """
        self._config = config
        self._repo = TickRepository(config.db_url)
        self._feed = MarketFeed(reconnect_base_delay=config.reconnect_base_delay)
        self._buffer: list[Tick] = []
        self._book_buffer: list[OrderBookSnapshot] = []
        self._shutdown = GracefulShutdown()
        self._heartbeat = HeartbeatLogger()
        self._ticks_since_heartbeat = 0
        self._total_ticks = 0
        self._asset_ids: list[str] = []
        self._condition_map: dict[str, str] = {}
        self._last_flush_time = 0.0

    async def run(self) -> None:
        """Execute the main collection loop until shutdown.

        Steps:
            1. Initialise the database schema.
            2. Discover markets from series slugs and static condition IDs.
            3. Resolve condition IDs to asset IDs.
            4. Connect to the WebSocket and stream trade events.
            5. Buffer ticks and flush on batch-size or timer triggers.
            6. Periodically re-discover markets and update subscriptions.
            7. On SIGINT/SIGTERM, flush remaining buffer and shut down.

        """
        self._shutdown.install()

        await self._repo.init_db()

        await self._discover_and_resolve()
        if not self._asset_ids:
            logger.error("No asset IDs discovered — nothing to subscribe to")
            return

        logger.info("Starting tick collection for %d assets", len(self._asset_ids))

        self._last_flush_time = time.monotonic()

        discovery_task = asyncio.create_task(self._periodic_discovery())
        heartbeat_task = asyncio.create_task(self._periodic_heartbeat())
        flush_task = asyncio.create_task(self._periodic_flush())
        book_poll_task = asyncio.create_task(self._periodic_book_poll())

        try:
            async for event in self._feed.stream(self._asset_ids):
                if self._shutdown.should_stop:
                    break
                self._handle_event(event)
                if len(self._buffer) >= self._config.flush_batch_size:
                    await self._flush_buffer()
        finally:
            discovery_task.cancel()
            heartbeat_task.cancel()
            flush_task.cancel()
            book_poll_task.cancel()
            await asyncio.gather(
                discovery_task,
                heartbeat_task,
                flush_task,
                book_poll_task,
                return_exceptions=True,
            )
            await self._flush_buffer()
            await self._flush_book_buffer()
            await self._feed.close()
            await self._repo.close()
            logger.info("Tick collector shut down — %d total ticks", self._total_ticks)

    def _handle_event(self, event: dict[str, Any]) -> None:
        """Parse a trade event and append a Tick to the buffer.

        Args:
            event: Parsed ``last_trade_price`` event from the WebSocket.

        """
        try:
            asset_id = str(event.get("asset_id", ""))
            tick = Tick(
                asset_id=asset_id,
                condition_id=self._condition_map.get(asset_id, ""),
                price=float(event.get("price", 0)),
                size=float(event.get("size", 0)),
                side=str(event.get("side", "")),
                fee_rate_bps=int(event.get("fee_rate_bps", 0)),
                timestamp=int(event.get("timestamp", 0)),
                received_at=now_ms(),
            )
            self._buffer.append(tick)
            self._ticks_since_heartbeat += 1
            self._total_ticks += 1
        except (ValueError, TypeError):
            logger.debug("Skipping malformed event: %s", event)

    async def _flush_buffer(self) -> None:
        """Write all buffered ticks to the database and clear the buffer."""
        if not self._buffer:
            return
        batch = list(self._buffer)
        self._buffer.clear()
        self._last_flush_time = time.monotonic()
        await self._repo.save_ticks(batch)

    async def _discover_and_resolve(self) -> None:
        """Discover markets from series slugs and resolve asset IDs.

        Combine static condition IDs from the config with dynamically
        discovered ones from series slugs.  Use a single client context
        for both discovery and resolution, fetch token IDs via the
        lightweight ``get_market_tokens()`` (no midpoint enrichment),
        and resolve all condition IDs concurrently with ``asyncio.gather()``.
        Pass ``include_next=True`` so upcoming 5-minute window markets
        are discovered before they open.
        """
        async with PolymarketClient() as client:
            condition_ids = list(self._config.markets)

            if self._config.series_slugs:
                try:
                    discovered = await client.discover_series_markets(
                        list(self._config.series_slugs),
                        include_next=True,
                    )
                    for cid, _end_date in discovered:
                        if cid not in condition_ids:
                            condition_ids.append(cid)
                    logger.info(
                        "Discovered %d markets from series slugs",
                        len(discovered),
                    )
                except (PolymarketError, KeyError, ValueError):
                    logger.exception("Series discovery failed")

            async def _resolve_one(cid: str) -> list[tuple[str, str]]:
                """Resolve a single condition ID to (token_id, cid) pairs."""
                try:
                    market = await client.get_market_tokens(cid)
                    return [
                        (token.token_id, cid)
                        for token in market.tokens
                        if token.token_id not in self._condition_map
                    ]
                except (PolymarketError, KeyError, ValueError):
                    logger.exception("Failed to resolve market %s", cid)
                    return []

            results = await asyncio.gather(*(_resolve_one(cid) for cid in condition_ids))

        new_asset_ids: list[str] = []
        for pairs in results:
            for token_id, cid in pairs:
                new_asset_ids.append(token_id)
                self._condition_map[token_id] = cid

        if new_asset_ids:
            added = [a for a in new_asset_ids if a not in self._asset_ids]
            self._asset_ids.extend(added)
            logger.info(
                "Resolved %d new asset IDs (total: %d)",
                len(added),
                len(self._asset_ids),
            )

    async def _periodic_discovery(self) -> None:
        """Re-discover markets aligned to 5-minute window boundaries.

        Sleep until ``discovery_lead_seconds`` before the next 5-minute
        boundary, then run discovery.  This ensures the collector subscribes
        to the next window's markets before they open, capturing ticks from
        the very start of each window.
        """
        try:
            await self._periodic_discovery_inner()
        except asyncio.CancelledError:
            return

    async def _periodic_discovery_inner(self) -> None:
        """Execute the discovery loop body (separated for CancelledError handling)."""
        while not self._shutdown.should_stop:
            sleep_seconds = _seconds_until_next_discovery(
                int(time.time()), self._config.discovery_lead_seconds
            )
            await asyncio.sleep(sleep_seconds)
            if self._shutdown.should_stop:
                break
            old_count = len(self._asset_ids)
            await self._discover_and_resolve()
            if len(self._asset_ids) > old_count:
                await self._feed.update_subscription(self._asset_ids)

    async def _periodic_heartbeat(self) -> None:
        """Log collection stats at regular intervals for monitoring.

        Emit a structured log line compatible with CloudWatch metric
        filters containing ticks-per-minute, total stored, and asset
        count.
        """
        try:
            while not self._shutdown.should_stop:
                await asyncio.sleep(60)
                if self._shutdown.should_stop:
                    break
                total = await self._repo.get_tick_count()
                self._heartbeat.maybe_log(
                    ticks_last_min=self._ticks_since_heartbeat,
                    total_stored=total,
                    assets=len(self._asset_ids),
                )
                self._ticks_since_heartbeat = 0
        except asyncio.CancelledError:
            return

    async def _periodic_flush(self) -> None:
        """Flush the buffer on a timer to bound write latency.

        Ensure ticks are written even during low-volume periods when
        the batch size threshold is not reached.
        """
        try:
            await self._periodic_flush_inner()
        except asyncio.CancelledError:
            return

    async def _periodic_flush_inner(self) -> None:
        """Execute the flush loop body (separated for CancelledError handling)."""
        while not self._shutdown.should_stop:
            await asyncio.sleep(self._config.flush_interval_seconds)
            if self._shutdown.should_stop:
                break
            elapsed = time.monotonic() - self._last_flush_time
            if elapsed >= self._config.flush_interval_seconds and self._buffer:
                await self._flush_buffer()

    def _serialize_order_book(
        self,
        token_id: str,
        timestamp_ms: int,
        book: OrderBook,
    ) -> OrderBookSnapshot:
        """Convert an ``OrderBook`` to an ORM snapshot with JSON-serialized levels.

        Truncate bids and asks to ``book_depth_levels`` and serialize each
        level as a ``[price, size]`` pair in JSON.

        Args:
            token_id: CLOB token identifier.
            timestamp_ms: Epoch milliseconds when the book was fetched.
            book: Typed order book from the Polymarket client.

        Returns:
            An ``OrderBookSnapshot`` ORM instance ready for persistence.

        """
        depth = self._config.book_depth_levels
        bids = [[str(lvl.price), str(lvl.size)] for lvl in book.bids[:depth]]
        asks = [[str(lvl.price), str(lvl.size)] for lvl in book.asks[:depth]]
        return OrderBookSnapshot(
            token_id=token_id,
            timestamp=timestamp_ms,
            bids_json=json.dumps(bids),
            asks_json=json.dumps(asks),
            spread=float(book.spread),
            midpoint=float(book.midpoint),
        )

    async def _flush_book_buffer(self) -> None:
        """Write all buffered order book snapshots to the database."""
        if not self._book_buffer:
            return
        batch = list(self._book_buffer)
        self._book_buffer.clear()
        await self._repo.save_order_book_snapshots(batch)
        logger.info("Flushed %d order book snapshots", len(batch))

    async def _periodic_book_poll(self) -> None:
        """Poll order books for all tracked tokens at a fixed interval.

        Skip entirely when ``book_poll_interval_seconds`` is 0 (disabled).
        Stagger requests across tokens by ``book_poll_stagger_ms`` to stay
        within CLOB rate limits.
        """
        try:
            await self._periodic_book_poll_inner()
        except asyncio.CancelledError:
            return

    async def _periodic_book_poll_inner(self) -> None:
        """Execute the book polling loop body."""
        if self._config.book_poll_interval_seconds <= 0:
            return
        stagger_s = self._config.book_poll_stagger_ms / MS_PER_SECOND
        while not self._shutdown.should_stop:
            await asyncio.sleep(self._config.book_poll_interval_seconds)
            if self._shutdown.should_stop:
                break
            async with PolymarketClient() as client:
                for token_id in list(self._asset_ids):
                    if self._shutdown.should_stop:
                        break
                    try:
                        book = await client.get_order_book(token_id)
                        snapshot = self._serialize_order_book(token_id, now_ms(), book)
                        self._book_buffer.append(snapshot)
                    except (PolymarketError, KeyError, ValueError):
                        logger.debug("Failed to poll order book for %s", token_id)
                    if stagger_s > 0:
                        await asyncio.sleep(stagger_s)
            await self._flush_book_buffer()
