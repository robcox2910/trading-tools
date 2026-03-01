"""Abstract base trading engine for shared event-loop infrastructure.

Factor out the price tracking, order book caching, market bootstrapping,
rotation, and WebSocket event handling that are identical between
``PaperTradingEngine`` and ``LiveTradingEngine``.  Subclasses implement
signal application, position management, and result building.
"""

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from collections import deque
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

import httpx

from trading_tools.apps.polymarket_bot.base_portfolio import BasePortfolio
from trading_tools.apps.polymarket_bot.models import (
    BotConfig,
    MarketSnapshot,
)
from trading_tools.apps.polymarket_bot.price_tracker import PriceTracker
from trading_tools.apps.polymarket_bot.protocols import PredictionMarketStrategy
from trading_tools.apps.tick_collector.ws_client import MarketFeed
from trading_tools.clients.polymarket.client import PolymarketClient
from trading_tools.clients.polymarket.exceptions import PolymarketAPIError
from trading_tools.clients.polymarket.models import Market
from trading_tools.core.models import Signal

if TYPE_CHECKING:
    from trading_tools.clients.polymarket.models import OrderBook

logger = logging.getLogger(__name__)

_MIN_TOKENS = 2
_FIVE_MINUTES = 300


class BaseTradingEngine[PortfolioT: BasePortfolio](ABC):
    """Template-method engine with shared event-loop infrastructure.

    Manage price tracking, order book caching, market bootstrapping via HTTP,
    periodic order book refresh, 5-minute window rotation for series markets,
    and WebSocket event dispatch.  Subclasses wire in their specific portfolio
    type, signal application logic, and result building.

    Args:
        client: Async Polymarket API client for fetching market data.
        strategy: Prediction market strategy that generates trading signals.
        config: Bot configuration (refresh intervals, capital, markets, etc.).
        portfolio: Portfolio instance for tracking positions and cash.
        feed: WebSocket market feed for streaming trade events.

    """

    def __init__(
        self,
        client: PolymarketClient,
        strategy: PredictionMarketStrategy,
        config: BotConfig,
        portfolio: PortfolioT,
        feed: MarketFeed | None = None,
    ) -> None:
        """Initialize shared engine state.

        Args:
            client: Async Polymarket API client.
            strategy: Prediction market strategy instance.
            config: Bot configuration.
            portfolio: Portfolio instance (paper or live).
            feed: Optional ``MarketFeed`` instance.  Created automatically
                if not provided.

        """
        self._client = client
        self._strategy = strategy
        self._config = config
        self._portfolio = portfolio
        self._feed = feed or MarketFeed()
        self._price_tracker = PriceTracker()
        self._active_markets: list[str] = list(config.markets)
        self._history: dict[str, deque[MarketSnapshot]] = {
            cid: deque(maxlen=config.max_history) for cid in self._active_markets
        }
        self._snapshots_processed = 0
        self._position_outcomes: dict[str, str] = {}
        self._end_time_overrides: dict[str, str] = dict(config.market_end_times)
        self._cached_order_books: dict[str, OrderBook] = {}
        self._cached_markets: dict[str, Market] = {}
        now = int(time.time())
        self._current_window: int = (now // _FIVE_MINUTES) * _FIVE_MINUTES
        self._asset_ids: list[str] = []

    # ------------------------------------------------------------------
    # Concrete shared methods
    # ------------------------------------------------------------------

    async def _bootstrap_market(self, condition_id: str) -> Market | None:
        """Fetch and register a single market with the price tracker.

        Retrieve the market from the API, validate it has at least two tokens,
        register it with the price tracker, set initial prices, and call the
        ``_on_bootstrap_market`` hook for subclass-specific setup.

        Callers are responsible for catching ``PolymarketAPIError`` and
        ``httpx.HTTPError``.

        Args:
            condition_id: Market condition identifier.

        Returns:
            The ``Market`` object on success, or ``None`` if the market
            has fewer than two tokens.

        """
        market: Market = await self._client.get_market(condition_id)

        if len(market.tokens) < _MIN_TOKENS:
            logger.warning("Market %s has fewer than 2 tokens", condition_id)
            return None

        yes_token = market.tokens[0]
        no_token = market.tokens[1]

        self._cached_markets[condition_id] = market
        self._price_tracker.register_market(condition_id, yes_token.token_id, no_token.token_id)
        self._price_tracker.update(yes_token.token_id, yes_token.price)
        self._price_tracker.update(no_token.token_id, no_token.price)

        self._on_bootstrap_market(condition_id, market)
        # Append asset IDs after the hook succeeds to avoid orphaned entries
        self._asset_ids.extend([yes_token.token_id, no_token.token_id])
        return market

    async def _bootstrap(self) -> None:
        """Fetch initial market data and order books via HTTP.

        Register all markets with the price tracker and populate the
        cached order books and market data.
        """
        for condition_id in self._active_markets:
            try:
                market = await self._bootstrap_market(condition_id)
            except (PolymarketAPIError, httpx.HTTPError):
                logger.warning("Failed to fetch market %s", condition_id, exc_info=True)
                continue

            if market is None:
                continue

            try:
                order_book = await self._client.get_order_book(market.tokens[0].token_id)
                self._cached_order_books[condition_id] = order_book
            except (PolymarketAPIError, httpx.HTTPError):
                logger.warning(
                    "Failed to fetch order book for %s",
                    condition_id,
                    exc_info=True,
                )

        logger.info(
            "Bootstrapped %d markets with %d asset IDs",
            len(self._cached_markets),
            len(self._asset_ids),
        )

    async def _on_price_update(self, event: dict[str, Any]) -> None:
        """Handle a WebSocket trade event.

        Update the price tracker, build a snapshot from cached data, and
        feed it to the strategy.

        Args:
            event: Parsed ``last_trade_price`` event from the WebSocket.

        """
        asset_id = str(event.get("asset_id", ""))
        try:
            price = Decimal(str(event.get("price", "0")))
        except (ValueError, InvalidOperation):
            logger.debug("Skipping event with invalid price: %s", event)
            return

        condition_id = self._price_tracker.update(asset_id, price)
        if condition_id is None:
            return

        if self._should_skip_market(condition_id):
            return

        snapshot = self._build_snapshot(condition_id)
        if snapshot is None:
            return

        self._snapshots_processed += 1
        market_history = self._history.setdefault(
            condition_id, deque(maxlen=self._config.max_history)
        )
        history = list(market_history)
        market_history.append(snapshot)

        logger.info(
            "[tick %d] %s YES=%.4f NO=%.4f bids=%d asks=%d",
            self._snapshots_processed,
            snapshot.question[:50],
            snapshot.yes_price,
            snapshot.no_price,
            len(snapshot.order_book.bids),
            len(snapshot.order_book.asks),
        )

        signal = self._strategy.on_snapshot(snapshot, history)
        if signal is not None:
            logger.info(
                "[tick %d] SIGNAL: %s %s strength=%.4f reason=%s",
                self._snapshots_processed,
                signal.side.name,
                signal.symbol[:20],
                signal.strength,
                signal.reason,
            )
            await self._apply_signal(signal, snapshot)
        else:
            logger.debug("[tick %d] No signal", self._snapshots_processed)

        outcome = self._position_outcomes.get(condition_id)
        if outcome is not None:
            mtm_price = snapshot.yes_price if outcome == "Yes" else snapshot.no_price
            self._portfolio.mark_to_market(condition_id, mtm_price)

    def _build_snapshot(self, condition_id: str) -> MarketSnapshot | None:
        """Build a MarketSnapshot from cached prices and order book.

        Args:
            condition_id: Market condition identifier.

        Returns:
            A ``MarketSnapshot`` or ``None`` if required data is missing.

        """
        prices = self._price_tracker.get_prices(condition_id)
        if prices is None:
            return None

        yes_price, no_price = prices
        if yes_price is None or no_price is None:
            return None

        order_book = self._cached_order_books.get(condition_id)
        if order_book is None:
            return None

        market = self._cached_markets.get(condition_id)
        if market is None:
            return None

        end_date = self._end_time_overrides.get(condition_id, market.end_date)

        return MarketSnapshot(
            condition_id=condition_id,
            question=market.question,
            timestamp=int(time.time()),
            yes_price=yes_price,
            no_price=no_price,
            order_book=order_book,
            volume=market.volume,
            liquidity=market.liquidity,
            end_date=end_date,
        )

    async def _refresh_order_books_loop(self) -> None:
        """Periodically refresh order books via HTTP in the background."""
        while True:
            await asyncio.sleep(self._config.order_book_refresh_seconds)
            for condition_id in self._active_markets:
                market = self._cached_markets.get(condition_id)
                if market is None or len(market.tokens) < _MIN_TOKENS:
                    continue
                try:
                    order_book = await self._client.get_order_book(market.tokens[0].token_id)
                    self._cached_order_books[condition_id] = order_book
                except (PolymarketAPIError, httpx.HTTPError):
                    logger.warning(
                        "Failed to refresh order book for %s",
                        condition_id,
                        exc_info=True,
                    )

    async def _rotation_loop(self) -> None:
        """Check for 5-minute window rotation periodically."""
        if not self._config.series_slugs:
            return
        while True:
            await asyncio.sleep(1)
            now = int(time.time())
            new_window = (now // _FIVE_MINUTES) * _FIVE_MINUTES
            if new_window != self._current_window:
                self._current_window = new_window
                await self._rotate_markets()

    async def _rotate_markets(self) -> None:
        """Re-discover active markets when the 5-minute window rotates.

        Call ``_on_rotation_close`` to handle open positions, then discover
        new markets from configured series slugs.  Clear all cached state,
        re-bootstrap each new market, update the WebSocket subscription,
        and log performance.
        """
        await self._on_rotation_close()

        try:
            discovered = await self._client.discover_series_markets(
                list(self._config.series_slugs),
            )
        except (PolymarketAPIError, httpx.HTTPError):
            logger.warning("Market rotation discovery failed", exc_info=True)
            return

        if not discovered:
            logger.warning("Market rotation found no new markets")
            return

        new_ids = [cid for cid, _ in discovered]
        self._active_markets = new_ids
        self._end_time_overrides = dict(discovered)

        # Clear price tracker and re-bootstrap for new markets
        self._price_tracker.clear()
        self._asset_ids.clear()
        self._cached_markets.clear()
        self._cached_order_books.clear()
        self._clear_market_state()

        for cid in new_ids:
            if cid not in self._history:
                self._history[cid] = deque(maxlen=self._config.max_history)
            try:
                market = await self._bootstrap_market(cid)
                if market is None:
                    continue
                order_book = await self._client.get_order_book(market.tokens[0].token_id)
                self._cached_order_books[cid] = order_book
            except (PolymarketAPIError, httpx.HTTPError):
                logger.warning(
                    "Failed to bootstrap rotated market %s",
                    cid,
                    exc_info=True,
                )

        await self._feed.update_subscription(self._asset_ids)

        logger.info(
            "Rotating markets: %d new condition IDs for window %d",
            len(new_ids),
            self._current_window,
        )
        self._log_performance()

    async def _refresh_order_book(self, condition_id: str) -> MarketSnapshot | None:
        """Fetch a fresh order book for a market and rebuild the snapshot.

        Call immediately before executing a trade so the order book data
        is current rather than up to ``order_book_refresh_seconds`` stale.

        Args:
            condition_id: Market condition identifier.

        Returns:
            Updated ``MarketSnapshot`` with the fresh order book,
            or ``None`` if the refresh fails.

        """
        market = self._cached_markets.get(condition_id)
        if market is None or len(market.tokens) < _MIN_TOKENS:
            return None

        try:
            order_book = await self._client.get_order_book(market.tokens[0].token_id)
            self._cached_order_books[condition_id] = order_book
            logger.info("Refreshed order book for %s before trade", condition_id[:20])
        except (PolymarketAPIError, httpx.HTTPError):
            logger.warning(
                "Failed to refresh order book for %s before trade, using cached",
                condition_id[:20],
                exc_info=True,
            )

        return self._build_snapshot(condition_id)

    # ------------------------------------------------------------------
    # Overridable hooks (default no-op)
    # ------------------------------------------------------------------

    def _on_bootstrap_market(  # noqa: B027
        self,
        condition_id: str,
        market: Market,
    ) -> None:
        """Store additional per-market state after bootstrap registration.

        Override in subclasses that need to track extra data per market
        (e.g., token IDs).  Default implementation is a no-op.

        Args:
            condition_id: Market condition identifier.
            market: The fetched ``Market`` object.

        """

    def _should_skip_market(self, condition_id: str) -> bool:  # noqa: ARG002
        """Return whether to skip a market during price-update processing.

        Override in subclasses that need to skip markets with open
        positions (e.g., live engine).

        Args:
            condition_id: Market condition identifier.

        Returns:
            ``True`` to skip processing this market.

        """
        return False

    def _clear_market_state(self) -> None:  # noqa: B027
        """Clear subclass-specific per-market state during rotation.

        Called after the shared caches are cleared but before
        re-bootstrapping.  Override in subclasses that maintain
        additional per-market state.
        """

    # ------------------------------------------------------------------
    # Abstract methods
    # ------------------------------------------------------------------

    @abstractmethod
    async def _apply_signal(self, signal: Signal, snapshot: MarketSnapshot) -> None:
        """Convert a strategy signal into a portfolio action.

        Args:
            signal: Trading signal from the strategy.
            snapshot: Current market snapshot.

        """

    @abstractmethod
    async def _on_rotation_close(self) -> None:
        """Handle open positions before market rotation.

        Called at the start of ``_rotate_markets`` before new markets
        are discovered.  Paper engines close positions at mark-to-market;
        live engines clear tracking and refresh balance.
        """

    @abstractmethod
    def _log_performance(self) -> None:
        """Log performance metrics at market rotation boundaries."""
