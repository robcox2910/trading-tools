"""Live market data adapter for the directional trading engine.

Compose ``MarketScanner``, ``PolymarketClient``, and
``BinanceCandleProvider`` to provide real-time market data through the
``MarketDataPort`` protocol.  Discover active Up/Down markets, fetch
order books, load Binance candles, and resolve market outcomes.

When an ``OrderBookFeed`` is provided, order book reads use the
WebSocket cache first and fall back to REST only when the cache is
stale or missing.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING

from trading_tools.apps.spread_capture.market_scanner import MarketScanner
from trading_tools.core.models import Interval

from .models import MarketOpportunity, TickSample

if TYPE_CHECKING:
    from collections.abc import Sequence

    from trading_tools.apps.tick_collector.repository import TickRepository
    from trading_tools.apps.whale_monitor.repository import WhaleRepository
    from trading_tools.clients.polymarket.client import PolymarketClient
    from trading_tools.clients.polymarket.models import OrderBook
    from trading_tools.core.models import Candle
    from trading_tools.data.providers.binance import BinanceCandleProvider
    from trading_tools.data.providers.order_book_feed import OrderBookFeed

logger = logging.getLogger(__name__)

_PER_SIDE_THRESHOLD = Decimal("1.00")


class LiveMarketData:
    """Live market data adapter that discovers markets and fetches real data.

    Compose a ``MarketScanner`` (for market discovery), ``PolymarketClient``
    (for order books and outcome resolution), and ``BinanceCandleProvider``
    (for 1-min candle data) into a single ``MarketDataPort`` implementation.

    When ``book_feed`` is supplied, ``get_order_books`` reads from the
    WebSocket cache first and only falls back to REST when the cached
    book is stale or missing.  ``get_active_markets`` eagerly syncs
    discovered token IDs into the feed's subscription.

    Args:
        client: Authenticated Polymarket client.
        candle_provider: Binance candle provider for historical data.
        series_slugs: Event series slugs to scan for markets.
        whale_repo: Optional whale trade repository for signal queries.
        book_feed: Optional WebSocket order book feed for low-latency reads.

    """

    def __init__(
        self,
        client: PolymarketClient,
        candle_provider: BinanceCandleProvider,
        series_slugs: tuple[str, ...] = ("btc-updown-5m", "eth-updown-5m"),
        whale_repo: WhaleRepository | None = None,
        book_feed: OrderBookFeed | None = None,
        tick_repo: TickRepository | None = None,
    ) -> None:
        """Initialize with API clients and create the market scanner.

        Args:
            client: Authenticated Polymarket client.
            candle_provider: Binance candle provider for fetching candles.
            series_slugs: Series slugs to scan for active markets.
            whale_repo: Optional whale trade repository for signal queries.
            book_feed: Optional WebSocket order book feed for low-latency reads.
            tick_repo: Optional tick repository for Polymarket tick data.

        """
        self._client = client
        self._candle_provider = candle_provider
        self._whale_repo = whale_repo
        self._book_feed = book_feed
        self._tick_repo = tick_repo
        self._scanner = MarketScanner(
            client=client,
            series_slugs=series_slugs,
            max_combined_cost=_PER_SIDE_THRESHOLD,
            min_spread_margin=Decimal(0),
            max_window_seconds=0,
            max_entry_age_pct=Decimal(0),
            rediscovery_interval=30,
        )

    async def get_active_markets(
        self,
        open_cids: set[str],
    ) -> list[MarketOpportunity]:
        """Scan for active Up/Down markets eligible for directional trading.

        Use the ``MarketScanner`` with a per-side threshold of $1.00 to
        get all active markets (no spread filtering — we want every market).
        Convert ``SpreadOpportunity`` objects to ``MarketOpportunity``.

        After discovery, eagerly sync discovered token IDs into the
        ``OrderBookFeed`` subscription so WebSocket data is available
        by the next poll cycle.

        Args:
            open_cids: Condition IDs of currently open positions.

        Returns:
            List of actionable market opportunities.

        """
        opportunities = await self._scanner.scan_per_side(open_cids, _PER_SIDE_THRESHOLD)
        markets = [
            MarketOpportunity(
                condition_id=opp.condition_id,
                title=opp.title,
                asset=opp.asset,
                up_token_id=opp.up_token_id,
                down_token_id=opp.down_token_id,
                window_start_ts=opp.window_start_ts,
                window_end_ts=opp.window_end_ts,
                up_price=opp.up_price,
                down_price=opp.down_price,
                up_ask_depth=opp.up_ask_depth,
                down_ask_depth=opp.down_ask_depth,
                series_slug=opp.series_slug,
            )
            for opp in opportunities
        ]

        await self._sync_book_feed(markets)
        return markets

    async def _sync_book_feed(
        self,
        markets: list[MarketOpportunity],
    ) -> None:
        """Update the book feed subscription with all discovered token IDs.

        Args:
            markets: Currently discovered market opportunities.

        """
        if self._book_feed is None:
            return

        token_ids: list[str] = []
        seen: set[str] = set()
        for m in markets:
            for tid in (m.up_token_id, m.down_token_id):
                if tid not in seen:
                    seen.add(tid)
                    token_ids.append(tid)

        if sorted(token_ids) != sorted(self._book_feed.subscribed_tokens):
            await self._book_feed.update_subscription(token_ids)

    async def get_order_books(
        self,
        up_token_id: str,
        down_token_id: str,
    ) -> tuple[OrderBook, OrderBook]:
        """Fetch live order books for both sides of a market.

        When a ``book_feed`` is available, read from the WebSocket cache
        first.  Fall back to REST for any token whose cached book is
        stale or missing.

        Args:
            up_token_id: CLOB token ID for the Up outcome.
            down_token_id: CLOB token ID for the Down outcome.

        Returns:
            Tuple of ``(up_book, down_book)``.

        """
        up_book = await self._get_single_book(up_token_id)
        down_book = await self._get_single_book(down_token_id)
        return up_book, down_book

    async def _get_single_book(self, token_id: str) -> OrderBook:
        """Return an order book, preferring the WS cache over REST.

        Args:
            token_id: CLOB token identifier.

        Returns:
            The order book from WebSocket cache or REST fallback.

        """
        if self._book_feed is not None and not self._book_feed.is_stale(token_id):
            book = self._book_feed.get_book(token_id)
            if book is not None:
                return book
        return await self._client.get_order_book(token_id)

    async def get_binance_candles(
        self,
        asset: str,
        start_ts: int,
        end_ts: int,
    ) -> Sequence[Candle]:
        """Fetch Binance 1-min candles for feature extraction.

        Args:
            asset: Spot trading pair (e.g. ``"BTC-USD"``).
            start_ts: Start epoch seconds (inclusive).
            end_ts: End epoch seconds (inclusive).

        Returns:
            List of 1-min candles ordered oldest to newest.

        """
        return await self._candle_provider.get_candles(
            symbol=asset,
            interval=Interval.M1,
            start_ts=start_ts,
            end_ts=end_ts,
        )

    async def get_whale_signal(
        self,
        condition_id: str,
    ) -> str | None:
        """Query whale net positioning from the whale trades database.

        Args:
            condition_id: Polymarket market condition identifier.

        Returns:
            ``"Up"`` or ``"Down"`` if whales have a directional bet,
            ``None`` if no whale repo or no activity.

        """
        if self._whale_repo is None:
            return None
        return await self._whale_repo.get_whale_signal(condition_id)

    async def get_recent_ticks(
        self,
        token_id: str,
        since_ms: int,
    ) -> list[TickSample]:
        """Fetch recent Polymarket ticks from the tick repository.

        Args:
            token_id: CLOB token identifier.
            since_ms: Epoch milliseconds — only return ticks after this.

        Returns:
            List of ``TickSample`` ordered by timestamp, or empty when
            no tick repository is configured.

        """
        if self._tick_repo is None:
            return []
        ticks = await self._tick_repo.get_ticks(token_id, since_ms, since_ms + 120_000)
        return [
            TickSample(
                price=t.price,
                size=t.size,
                side=t.side,
                timestamp_ms=t.timestamp,
            )
            for t in ticks
        ]

    async def resolve_outcome(
        self,
        opportunity: MarketOpportunity,
    ) -> str | None:
        """Determine the winning side via Binance candle data.

        Fetch 1-min candles spanning the market window and compare
        the first open to the last close.

        Args:
            opportunity: The market to resolve.

        Returns:
            ``"Up"`` or ``"Down"`` if resolved, ``None`` otherwise.

        """
        candles = await self._candle_provider.get_candles(
            symbol=opportunity.asset,
            interval=Interval.M1,
            start_ts=opportunity.window_start_ts,
            end_ts=opportunity.window_end_ts,
        )
        if not candles:
            return None
        open_price = candles[0].open
        close_price = candles[-1].close
        if close_price > open_price:
            return "Up"
        if close_price < open_price:
            return "Down"
        return None
