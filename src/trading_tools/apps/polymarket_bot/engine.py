"""Async paper trading engine for prediction markets.

Implement the core polling loop that fetches market data, builds snapshots,
feeds them to a strategy, sizes positions with Kelly criterion, and tracks
virtual P&L through a paper portfolio.
"""

import asyncio
import logging
import time
from collections import deque
from decimal import Decimal
from typing import TYPE_CHECKING

from trading_tools.apps.polymarket_bot.kelly import kelly_fraction
from trading_tools.apps.polymarket_bot.models import (
    BotConfig,
    MarketSnapshot,
    PaperTradingResult,
)
from trading_tools.apps.polymarket_bot.portfolio import PaperPortfolio
from trading_tools.apps.polymarket_bot.protocols import PredictionMarketStrategy
from trading_tools.clients.polymarket.client import PolymarketClient
from trading_tools.core.models import ZERO, Side, Signal

if TYPE_CHECKING:
    from trading_tools.clients.polymarket.models import Market

logger = logging.getLogger(__name__)

_TIMESTAMP_PLACEHOLDER = 0


class PaperTradingEngine:
    """Async polling engine that wires strategy, Kelly sizer, and portfolio.

    Fetch market data at a configurable interval, build ``MarketSnapshot``
    objects, feed them to the configured strategy, and execute virtual trades
    sized by the Kelly criterion. Track all positions and trades through a
    ``PaperPortfolio``.

    Args:
        client: Async Polymarket API client for fetching market data.
        strategy: Prediction market strategy that generates trading signals.
        config: Bot configuration (poll interval, capital, markets, etc.).

    """

    def __init__(
        self,
        client: PolymarketClient,
        strategy: PredictionMarketStrategy,
        config: BotConfig,
    ) -> None:
        """Initialize the paper trading engine.

        Args:
            client: Async Polymarket API client.
            strategy: Prediction market strategy instance.
            config: Bot configuration.

        """
        self._client = client
        self._strategy = strategy
        self._config = config
        self._portfolio = PaperPortfolio(config.initial_capital, config.max_position_pct)
        self._history: dict[str, deque[MarketSnapshot]] = {
            cid: deque(maxlen=config.max_history) for cid in config.markets
        }
        self._snapshots_processed = 0

    async def run(self, *, max_ticks: int | None = None) -> PaperTradingResult:
        """Execute the polling loop until stopped or max_ticks reached.

        Each tick fetches market data, builds snapshots, feeds them to the
        strategy, and processes any resulting signals.

        Args:
            max_ticks: Stop after this many ticks (``None`` for unlimited).

        Returns:
            Summary of the paper trading run including trades and metrics.

        """
        tick_count = 0
        while max_ticks is None or tick_count < max_ticks:
            await self._tick()
            tick_count += 1
            if max_ticks is not None and tick_count >= max_ticks:
                break
            await asyncio.sleep(self._config.poll_interval_seconds)

        return self._build_result()

    async def _fetch_snapshot(self, condition_id: str) -> MarketSnapshot | None:
        """Fetch market data and build a MarketSnapshot.

        Args:
            condition_id: Market condition identifier.

        Returns:
            A ``MarketSnapshot`` or ``None`` if the fetch fails.

        """
        try:
            market: Market = await self._client.get_market(condition_id)
        except Exception:
            logger.warning("Failed to fetch market %s", condition_id)
            return None

        yes_token = next((t for t in market.tokens if t.outcome.lower() == "yes"), None)
        no_token = next((t for t in market.tokens if t.outcome.lower() == "no"), None)

        if yes_token is None or no_token is None:
            logger.warning("Market %s missing YES/NO tokens", condition_id)
            return None

        try:
            order_book = await self._client.get_order_book(yes_token.token_id)
        except Exception:
            logger.warning("Failed to fetch order book for %s", condition_id)
            return None

        return MarketSnapshot(
            condition_id=condition_id,
            question=market.question,
            timestamp=int(time.time()),
            yes_price=yes_token.price,
            no_price=no_token.price,
            order_book=order_book,
            volume=market.volume,
            liquidity=market.liquidity,
            end_date=market.end_date,
        )

    async def _tick(self) -> None:
        """Execute one polling cycle across all tracked markets."""
        for condition_id in self._config.markets:
            snapshot = await self._fetch_snapshot(condition_id)
            if snapshot is None:
                continue

            self._snapshots_processed += 1
            history = list(self._history[condition_id])
            self._history[condition_id].append(snapshot)

            signal = self._strategy.on_snapshot(snapshot, history)
            if signal is not None:
                self._apply_signal(signal, snapshot)

            self._portfolio.mark_to_market(condition_id, snapshot.yes_price)

    def _apply_signal(self, signal: Signal, snapshot: MarketSnapshot) -> None:
        """Convert a strategy signal into a portfolio action.

        Size the position using the Kelly criterion and execute via the
        paper portfolio.

        Args:
            signal: Trading signal from the strategy.
            snapshot: Current market snapshot.

        """
        condition_id = snapshot.condition_id

        if signal.side == Side.SELL and condition_id in self._portfolio.positions:
            self._portfolio.close_position(condition_id, snapshot.yes_price, snapshot.timestamp)
            return

        if signal.side == Side.BUY and condition_id not in self._portfolio.positions:
            estimated_prob = snapshot.yes_price + signal.strength * (
                Decimal(1) - snapshot.yes_price
            ) * Decimal("0.1")
            estimated_prob = min(estimated_prob, Decimal("0.99"))

            fraction = kelly_fraction(
                estimated_prob,
                snapshot.yes_price,
                fractional=self._config.kelly_fraction,
            )

            if fraction <= ZERO:
                return

            max_qty = self._portfolio.max_quantity_for(snapshot.yes_price)
            quantity = max(Decimal(1), (max_qty * fraction).quantize(Decimal(1)))

            edge = estimated_prob - snapshot.yes_price
            self._portfolio.open_position(
                condition_id=condition_id,
                outcome="Yes",
                side=Side.BUY,
                price=snapshot.yes_price,
                quantity=quantity,
                timestamp=snapshot.timestamp,
                reason=signal.reason,
                edge=edge,
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
