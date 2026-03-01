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
from decimal import Decimal

from trading_tools.apps.polymarket_bot.base_engine import BaseTradingEngine
from trading_tools.apps.polymarket_bot.kelly import kelly_fraction
from trading_tools.apps.polymarket_bot.models import (
    BotConfig,
    MarketSnapshot,
    PaperTradingResult,
)
from trading_tools.apps.polymarket_bot.portfolio import PaperPortfolio
from trading_tools.apps.polymarket_bot.protocols import PredictionMarketStrategy
from trading_tools.apps.tick_collector.ws_client import MarketFeed
from trading_tools.clients.polymarket.client import PolymarketClient
from trading_tools.core.models import ZERO, Side, Signal

logger = logging.getLogger(__name__)

_MIN_ORDER_SIZE = Decimal(5)


class PaperTradingEngine(BaseTradingEngine[PaperPortfolio]):
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
        portfolio = PaperPortfolio(config.initial_capital, config.max_position_pct)
        super().__init__(client, strategy, config, portfolio, feed)

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

    async def _on_rotation_close(self) -> None:
        """Close all open paper positions at mark-to-market prices."""
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
