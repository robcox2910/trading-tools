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
_MIN_TOKENS = 2
_FIVE_MINUTES = 300
_DEFAULT_PERF_LOG_INTERVAL = 50


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
        perf_log_interval: Log performance metrics every N ticks.

    """

    def __init__(
        self,
        client: PolymarketClient,
        strategy: PredictionMarketStrategy,
        config: BotConfig,
        *,
        perf_log_interval: int = _DEFAULT_PERF_LOG_INTERVAL,
    ) -> None:
        """Initialize the paper trading engine.

        Args:
            client: Async Polymarket API client.
            strategy: Prediction market strategy instance.
            config: Bot configuration.
            perf_log_interval: Log performance metrics every N ticks.

        """
        self._client = client
        self._strategy = strategy
        self._config = config
        self._perf_log_interval = perf_log_interval
        self._portfolio = PaperPortfolio(config.initial_capital, config.max_position_pct)
        self._active_markets: list[str] = list(config.markets)
        self._history: dict[str, deque[MarketSnapshot]] = {
            cid: deque(maxlen=config.max_history) for cid in self._active_markets
        }
        self._snapshots_processed = 0
        self._position_outcomes: dict[str, str] = {}
        self._end_time_overrides: dict[str, str] = dict(config.market_end_times)
        now = int(time.time())
        self._current_window: int = (now // _FIVE_MINUTES) * _FIVE_MINUTES

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
            if tick_count % self._perf_log_interval == 0:
                self._log_performance(tick_count)
            if max_ticks is not None and tick_count >= max_ticks:
                break
            await asyncio.sleep(self._config.poll_interval_seconds)

        return self._build_result()

    def _log_performance(self, tick_count: int) -> None:
        """Log periodic performance metrics.

        Emit an INFO log line with equity, cash, position count, trade count,
        and return percentage so that long-running bots can be monitored via
        log files or CloudWatch without stopping the engine.

        Args:
            tick_count: Current tick number.

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
            tick_count,
            equity,
            cash,
            positions,
            trades,
            ret,
        )

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
            logger.warning("Failed to fetch market %s", condition_id, exc_info=True)
            return None

        # Polymarket markets use "Yes"/"No" or "Up"/"Down" outcome names.
        # The first token is the primary outcome (YES/Up), second is the complement.
        if len(market.tokens) < _MIN_TOKENS:
            logger.warning("Market %s has fewer than 2 tokens", condition_id)
            return None
        yes_token = market.tokens[0]
        no_token = market.tokens[1]

        try:
            order_book = await self._client.get_order_book(yes_token.token_id)
        except Exception:
            logger.warning("Failed to fetch order book for %s", condition_id, exc_info=True)
            return None

        end_date = self._end_time_overrides.get(condition_id, market.end_date)

        return MarketSnapshot(
            condition_id=condition_id,
            question=market.question,
            timestamp=int(time.time()),
            yes_price=yes_token.price,
            no_price=no_token.price,
            order_book=order_book,
            volume=market.volume,
            liquidity=market.liquidity,
            end_date=end_date,
        )

    async def _rotate_markets(self) -> None:
        """Re-discover active markets when the 5-minute window rotates.

        Close all open positions at current mark-to-market prices (the
        previous window's markets have resolved), then call the client
        to discover new condition IDs for the current window. Update
        ``_active_markets``, ``_end_time_overrides``, and initialize
        fresh history deques for the new markets.
        """
        # Close all open positions — previous window resolved
        for cid in list(self._portfolio.positions):
            outcome = self._position_outcomes.get(cid, "Yes")
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
        except Exception:
            logger.warning("Market rotation discovery failed", exc_info=True)
            return

        if not discovered:
            logger.warning("Market rotation found no new markets")
            return

        new_ids = [cid for cid, _ in discovered]
        self._active_markets = new_ids
        self._end_time_overrides = dict(discovered)
        for cid in new_ids:
            if cid not in self._history:
                self._history[cid] = deque(maxlen=self._config.max_history)

        logger.info(
            "Rotating markets: %d new condition IDs for window %d",
            len(new_ids),
            self._current_window,
        )

    async def _tick(self) -> None:
        """Execute one polling cycle across all tracked markets."""
        # Check for 5-minute window rotation when series slugs are configured
        if self._config.series_slugs:
            now = int(time.time())
            new_window = (now // _FIVE_MINUTES) * _FIVE_MINUTES
            if new_window != self._current_window:
                self._current_window = new_window
                await self._rotate_markets()

        for condition_id in self._active_markets:
            snapshot = await self._fetch_snapshot(condition_id)
            if snapshot is None:
                continue

            self._snapshots_processed += 1
            market_history = self._history.setdefault(
                condition_id, deque(maxlen=self._config.max_history)
            )
            history = list(market_history)
            market_history.append(snapshot)

            logger.info(
                "[tick %d] %s YES=%.4f NO=%.4f vol=%s liq=%s bids=%d asks=%d",
                self._snapshots_processed,
                snapshot.question[:50],
                snapshot.yes_price,
                snapshot.no_price,
                snapshot.volume,
                snapshot.liquidity,
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
                self._apply_signal(signal, snapshot)
            else:
                logger.info("[tick %d] No signal", self._snapshots_processed)

            outcome = self._position_outcomes.get(condition_id, "Yes")
            mtm_price = snapshot.yes_price if outcome == "Yes" else snapshot.no_price
            self._portfolio.mark_to_market(condition_id, mtm_price)

    def _apply_signal(self, signal: Signal, snapshot: MarketSnapshot) -> None:
        """Convert a strategy signal into a portfolio action.

        Size the position using the Kelly criterion and execute via the
        paper portfolio. A SELL signal on a market with no open position
        is interpreted as "buy the NO/Down token" (the complement outcome).

        Args:
            signal: Trading signal from the strategy.
            snapshot: Current market snapshot.

        """
        condition_id = snapshot.condition_id

        # Close existing position
        if signal.side == Side.SELL and condition_id in self._portfolio.positions:
            outcome = self._position_outcomes.get(condition_id, "Yes")
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
        estimated_prob = buy_price + signal.strength * (Decimal(1) - buy_price)
        estimated_prob = min(estimated_prob, Decimal("0.99"))

        fraction = kelly_fraction(
            estimated_prob,
            buy_price,
            fractional=self._config.kelly_fraction,
        )

        if fraction <= ZERO:
            return

        max_qty = self._portfolio.max_quantity_for(buy_price)
        quantity = max(Decimal(1), (max_qty * fraction).quantize(Decimal(1)))

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
