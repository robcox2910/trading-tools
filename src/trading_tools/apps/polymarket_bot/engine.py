"""Async paper trading engine for prediction markets.

Implement a WebSocket-driven event loop that receives real-time trade prices
from ``MarketFeed``, builds market snapshots, feeds them to a strategy, sizes
positions with Kelly criterion, and tracks virtual P&L through a paper
portfolio. Order books are refreshed periodically in the background since
the WebSocket only provides last trade prices.
"""

import asyncio
import logging
import time
from collections import deque
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

import httpx

from trading_tools.apps.polymarket_bot.kelly import kelly_fraction
from trading_tools.apps.polymarket_bot.models import (
    BotConfig,
    MarketSnapshot,
    PaperTradingResult,
)
from trading_tools.apps.polymarket_bot.portfolio import PaperPortfolio
from trading_tools.apps.polymarket_bot.price_tracker import PriceTracker
from trading_tools.apps.polymarket_bot.protocols import PredictionMarketStrategy
from trading_tools.apps.tick_collector.ws_client import MarketFeed
from trading_tools.clients.polymarket.client import PolymarketClient
from trading_tools.clients.polymarket.exceptions import PolymarketAPIError
from trading_tools.core.models import ZERO, Side, Signal

if TYPE_CHECKING:
    from trading_tools.clients.polymarket.models import Market, OrderBook

logger = logging.getLogger(__name__)

_MIN_TOKENS = 2
_MIN_ORDER_SIZE = Decimal(5)
_FIVE_MINUTES = 300


class PaperTradingEngine:
    """WebSocket-driven engine that wires strategy, Kelly sizer, and portfolio.

    Receive real-time trade prices from ``MarketFeed``, maintain cached order
    books via periodic HTTP refresh, build ``MarketSnapshot`` objects, feed
    them to the configured strategy, and execute virtual trades sized by the
    Kelly criterion. Track all positions and trades through a
    ``PaperPortfolio``.

    Args:
        client: Async Polymarket API client for fetching market data.
        strategy: Prediction market strategy that generates trading signals.
        config: Bot configuration (refresh intervals, capital, markets, etc.).
        feed: WebSocket market feed for streaming trade events.

    """

    def __init__(
        self,
        client: PolymarketClient,
        strategy: PredictionMarketStrategy,
        config: BotConfig,
        feed: MarketFeed | None = None,
    ) -> None:
        """Initialize the paper trading engine.

        Args:
            client: Async Polymarket API client.
            strategy: Prediction market strategy instance.
            config: Bot configuration.
            feed: Optional ``MarketFeed`` instance. Created automatically
                if not provided.

        """
        self._client = client
        self._strategy = strategy
        self._config = config
        self._feed = feed or MarketFeed()
        self._portfolio = PaperPortfolio(config.initial_capital, config.max_position_pct)
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

    async def run(self, *, max_ticks: int | None = None) -> PaperTradingResult:
        """Execute the WebSocket event loop until stopped or max_ticks reached.

        Bootstrap initial state via HTTP (fetch markets and order books),
        then stream trade events from ``MarketFeed``. Background tasks
        refresh order books and rotate markets periodically.

        Args:
            max_ticks: Stop after this many price events (``None`` for unlimited).

        Returns:
            Summary of the paper trading run including trades and metrics.

        """
        await self._bootstrap()

        if not self._asset_ids:
            return self._build_result()

        ob_task = asyncio.create_task(self._refresh_order_books_loop())
        rotation_task = asyncio.create_task(self._rotation_loop())

        tick_count = 0
        try:
            async for event in self._feed.stream(self._asset_ids):
                await self._on_price_update(event)
                tick_count += 1
                if max_ticks is not None and tick_count >= max_ticks:
                    break
        finally:
            ob_task.cancel()
            rotation_task.cancel()
            await self._feed.close()

        return self._build_result()

    async def _bootstrap(self) -> None:
        """Fetch initial market data and order books via HTTP.

        Register all markets with the price tracker and populate the
        cached order books and market data.
        """
        for condition_id in self._active_markets:
            try:
                market: Market = await self._client.get_market(condition_id)
            except (PolymarketAPIError, httpx.HTTPError):
                logger.warning("Failed to fetch market %s", condition_id, exc_info=True)
                continue

            if len(market.tokens) < _MIN_TOKENS:
                logger.warning("Market %s has fewer than 2 tokens", condition_id)
                continue

            yes_token = market.tokens[0]
            no_token = market.tokens[1]

            self._cached_markets[condition_id] = market
            self._price_tracker.register_market(condition_id, yes_token.token_id, no_token.token_id)
            self._asset_ids.extend([yes_token.token_id, no_token.token_id])

            # Set initial prices from HTTP
            self._price_tracker.update(yes_token.token_id, yes_token.price)
            self._price_tracker.update(no_token.token_id, no_token.price)

            try:
                order_book = await self._client.get_order_book(yes_token.token_id)
                self._cached_order_books[condition_id] = order_book
            except (PolymarketAPIError, httpx.HTTPError):
                logger.warning("Failed to fetch order book for %s", condition_id, exc_info=True)

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
            logger.info("[tick %d] No signal", self._snapshots_processed)

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

        Close all open positions at current mark-to-market prices (the
        previous window's markets have resolved), then call the client
        to discover new condition IDs. Update the price tracker, cached
        data, and WebSocket subscription.
        """
        # Close all open positions
        for cid in list(self._portfolio.positions):
            outcome = self._position_outcomes.get(cid, "Yes")  # safe: only open positions tracked
            last_snap = self._history.get(cid)
            if last_snap:
                latest = last_snap[-1]
                close_price = latest.yes_price if outcome == "Yes" else latest.no_price
            else:
                close_price = Decimal("0.50")
                logger.warning(
                    "No price history for %s, using fallback price 0.50",
                    cid[:20],
                )
            trade = self._portfolio.close_position(cid, close_price, int(time.time()))
            if trade is not None:
                logger.info(
                    "ROTATION CLOSE: %s @ %.4f (window expired)",
                    cid[:20],
                    close_price,
                )
            self._position_outcomes.pop(cid, None)

        # Discover new markets
        try:
            discovered = await self._client.discover_series_markets(list(self._config.series_slugs))
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

        for cid in new_ids:
            if cid not in self._history:
                self._history[cid] = deque(maxlen=self._config.max_history)
            try:
                market = await self._client.get_market(cid)
                if len(market.tokens) < _MIN_TOKENS:
                    continue
                self._cached_markets[cid] = market
                yes_tok = market.tokens[0]
                no_tok = market.tokens[1]
                self._price_tracker.register_market(cid, yes_tok.token_id, no_tok.token_id)
                self._asset_ids.extend([yes_tok.token_id, no_tok.token_id])
                self._price_tracker.update(yes_tok.token_id, yes_tok.price)
                self._price_tracker.update(no_tok.token_id, no_tok.price)

                order_book = await self._client.get_order_book(yes_tok.token_id)
                self._cached_order_books[cid] = order_book
            except (PolymarketAPIError, httpx.HTTPError):
                logger.warning("Failed to bootstrap rotated market %s", cid, exc_info=True)

        await self._feed.update_subscription(self._asset_ids)

        logger.info(
            "Rotating markets: %d new condition IDs for window %d",
            len(new_ids),
            self._current_window,
        )
        self._log_performance()

    def _log_performance(self) -> None:
        """Log performance metrics at market rotation boundaries.

        Emit an INFO log line with equity, cash, position count, trade count,
        and return percentage so that long-running bots can be monitored via
        log files or CloudWatch without stopping the engine.
        """
        equity = self._portfolio.total_equity
        cash = self._portfolio.capital
        positions = len(self._portfolio.positions)
        trades = len(self._portfolio.trades)
        ret = (
            (equity - self._config.initial_capital) / self._config.initial_capital * 100
            if self._config.initial_capital > ZERO
            else ZERO
        )
        logger.info(
            "[PERF tick=%d] equity=$%.2f cash=$%.2f positions=%d trades=%d return=%+.2f%%",
            self._snapshots_processed,
            equity,
            cash,
            positions,
            trades,
            ret,
        )

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

    async def _apply_signal(self, signal: Signal, snapshot: MarketSnapshot) -> None:
        """Convert a strategy signal into a portfolio action.

        Refresh the order book before executing so position sizing and
        price data are current. Size the position using the Kelly criterion
        and execute via the paper portfolio. A SELL signal on a market with
        no open position is interpreted as "buy the NO/Down token"
        (the complement outcome).

        Args:
            signal: Trading signal from the strategy.
            snapshot: Current market snapshot.

        """
        # Refresh order book to get current bid/ask before trading
        fresh_snapshot = await self._refresh_order_book(snapshot.condition_id)
        if fresh_snapshot is not None:
            snapshot = fresh_snapshot

        condition_id = snapshot.condition_id

        # Close existing position
        if signal.side == Side.SELL and condition_id in self._portfolio.positions:
            outcome = self._position_outcomes.get(condition_id, "Yes")  # safe: checked above
            close_price = snapshot.yes_price if outcome == "Yes" else snapshot.no_price
            trade = self._portfolio.close_position(condition_id, close_price, snapshot.timestamp)
            if trade is not None:
                logger.info(
                    "[tick %d] POSITION CLOSED: %s @ %.4f",
                    self._snapshots_processed,
                    condition_id[:20],
                    close_price,
                )
            self._position_outcomes.pop(condition_id, None)
            return

        # Open new position (BUY → first token, SELL without position → second token)
        if condition_id not in self._portfolio.positions:
            if signal.side == Side.BUY:
                buy_price = snapshot.yes_price
                outcome = "Yes"
            elif signal.side == Side.SELL:
                buy_price = snapshot.no_price
                outcome = "No"
            else:
                return

            self._open_position(condition_id, outcome, buy_price, snapshot, signal)

    def _open_position(
        self,
        condition_id: str,
        outcome: str,
        buy_price: Decimal,
        snapshot: MarketSnapshot,
        signal: Signal,
    ) -> None:
        """Open a new paper position on the given outcome token.

        Size the position with Kelly criterion and record which outcome
        token was purchased for correct mark-to-market accounting.

        Args:
            condition_id: Market condition identifier.
            outcome: Token outcome ("Yes" or "No").
            buy_price: Price at which to buy the token.
            snapshot: Current market snapshot.
            signal: Strategy signal that triggered the trade.

        """
        estimated_prob = max(
            buy_price + signal.strength * (Decimal(1) - buy_price),
            buy_price + self._config.min_edge,
        )
        estimated_prob = min(estimated_prob, Decimal("0.99"))

        fraction = kelly_fraction(
            estimated_prob,
            buy_price,
            fractional=self._config.kelly_fraction,
        )

        if fraction <= ZERO:
            return

        max_qty = self._portfolio.max_quantity_for(buy_price)
        quantity = max(_MIN_ORDER_SIZE, (max_qty * fraction).quantize(Decimal(1)))

        edge = estimated_prob - buy_price
        trade = self._portfolio.open_position(
            condition_id=condition_id,
            outcome=outcome,
            side=Side.BUY,
            price=buy_price,
            quantity=quantity,
            timestamp=snapshot.timestamp,
            reason=signal.reason,
            edge=edge,
        )
        if trade is not None:
            logger.info(
                "[tick %d] TRADE OPENED: %s %s qty=%s @ %.4f edge=%.4f",
                self._snapshots_processed,
                outcome,
                condition_id[:20],
                quantity,
                buy_price,
                edge,
            )
            self._position_outcomes[condition_id] = outcome
        else:
            logger.warning(
                "[tick %d] TRADE REJECTED: %s (duplicate or insufficient capital)",
                self._snapshots_processed,
                condition_id[:20],
            )

    def _build_result(self) -> PaperTradingResult:
        """Build the final result from the portfolio state.

        Returns:
            Summary of the paper trading run.

        """
        trades = self._portfolio.trades
        final_capital = self._portfolio.total_equity

        metrics: dict[str, Decimal] = {}
        if trades:
            buy_trades = [t for t in trades if t.side == Side.BUY]
            sell_trades = [t for t in trades if t.side == Side.SELL]
            metrics["total_trades"] = Decimal(len(trades))
            metrics["buy_trades"] = Decimal(len(buy_trades))
            metrics["sell_trades"] = Decimal(len(sell_trades))
            metrics["total_return"] = (
                (final_capital - self._config.initial_capital) / self._config.initial_capital
                if self._config.initial_capital > ZERO
                else ZERO
            )

        return PaperTradingResult(
            strategy_name=self._strategy.name,
            initial_capital=self._config.initial_capital,
            final_capital=final_capital,
            trades=tuple(trades),
            snapshots_processed=self._snapshots_processed,
            metrics=metrics,
        )
