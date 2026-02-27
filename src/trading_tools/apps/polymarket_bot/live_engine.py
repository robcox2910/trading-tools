"""Async live trading engine for prediction markets.

Implement the core polling loop that fetches market data, builds snapshots,
feeds them to a strategy, sizes positions with Kelly criterion, and executes
real trades through the Polymarket CLOB API via a ``LivePortfolio``.

Safety guardrails include a configurable loss limit, balance checks before
every trade, graceful shutdown on SIGINT, and automatic position closing
on exit.
"""

import asyncio
import logging
import signal
import time
from collections import deque
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from trading_tools.apps.polymarket_bot.kelly import kelly_fraction
from trading_tools.apps.polymarket_bot.live_portfolio import LivePortfolio
from trading_tools.apps.polymarket_bot.models import (
    BotConfig,
    LiveTradingResult,
    MarketSnapshot,
)
from trading_tools.apps.polymarket_bot.protocols import PredictionMarketStrategy
from trading_tools.clients.polymarket.client import PolymarketClient
from trading_tools.core.models import ZERO, Side, Signal

if TYPE_CHECKING:
    from trading_tools.clients.polymarket.models import Market

logger = logging.getLogger(__name__)

_MIN_TOKENS = 2
_MIN_ORDER_SIZE = Decimal(5)
_FIVE_MINUTES = 300
_SLEEP_BUFFER_SECONDS = 5
_DEFAULT_MAX_LOSS_PCT = Decimal("0.10")


class LiveTradingEngine:
    """Async polling engine that executes real trades on Polymarket.

    Fetch market data at a configurable interval, build ``MarketSnapshot``
    objects, feed them to the configured strategy, size positions with the
    Kelly criterion, and execute trades via the CLOB API through a
    ``LivePortfolio``.

    Safety guardrails:
    - Loss limit stops the engine when equity drops below threshold
    - Balance refreshed from CLOB before every trade
    - SIGINT handler for graceful shutdown with position closing
    - All open positions closed on exit

    Args:
        client: Authenticated async Polymarket API client.
        strategy: Prediction market strategy that generates trading signals.
        config: Bot configuration (poll interval, capital, markets, etc.).
        max_loss_pct: Maximum drawdown fraction before auto-stop (default 10%).
        use_market_orders: Use FOK market orders (default) or GTC limit.

    """

    def __init__(
        self,
        client: PolymarketClient,
        strategy: PredictionMarketStrategy,
        config: BotConfig,
        *,
        max_loss_pct: Decimal = _DEFAULT_MAX_LOSS_PCT,
        use_market_orders: bool = True,
        auto_redeem: bool = False,
    ) -> None:
        """Initialize the live trading engine.

        Args:
            client: Authenticated async Polymarket API client.
            strategy: Prediction market strategy instance.
            config: Bot configuration.
            max_loss_pct: Maximum allowed loss fraction (0-1).
            use_market_orders: Use FOK market orders or GTC limit orders.
            auto_redeem: Attempt to redeem resolved positions on rotation.
                Require POL in the signing EOA for gas.

        """
        self._client = client
        self._strategy = strategy
        self._config = config
        self._max_loss_pct = max_loss_pct
        self._auto_redeem = auto_redeem
        self._portfolio = LivePortfolio(
            client,
            config.max_position_pct,
            use_market_orders=use_market_orders,
        )
        self._active_markets: list[str] = list(config.markets)
        self._history: dict[str, deque[MarketSnapshot]] = {
            cid: deque(maxlen=config.max_history) for cid in self._active_markets
        }
        self._snapshots_processed = 0
        self._position_outcomes: dict[str, str] = {}
        self._token_ids: dict[str, tuple[str, str]] = {}
        self._end_time_overrides: dict[str, str] = dict(config.market_end_times)
        now = int(time.time())
        self._current_window: int = (now // _FIVE_MINUTES) * _FIVE_MINUTES
        self._initial_balance = ZERO
        self._shutdown = False
        self._redeem_task: asyncio.Task[None] | None = None

    async def run(self, *, max_ticks: int | None = None) -> LiveTradingResult:
        """Execute the polling loop until stopped, loss limit hit, or max_ticks reached.

        Install a SIGINT handler for graceful shutdown. On exit, close all
        open positions before returning the result.

        Args:
            max_ticks: Stop after this many ticks (``None`` for unlimited).

        Returns:
            Summary of the live trading run including trades and metrics.

        """
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGINT, self._handle_sigint)

        self._initial_balance = await self._portfolio.refresh_balance()
        logger.info("Initial USDC balance: %s", self._initial_balance)

        tick_count = 0
        while max_ticks is None or tick_count < max_ticks:
            if self._shutdown:
                logger.info("Shutdown signal received, closing positions...")
                break

            if self._check_loss_limit():
                logger.warning(
                    "Loss limit reached (%.1f%%), stopping engine",
                    float(self._max_loss_pct * Decimal(100)),
                )
                break

            await self._tick()
            tick_count += 1
            if max_ticks is not None and tick_count >= max_ticks:
                break
            sleep_seconds = self._compute_sleep()
            await asyncio.sleep(sleep_seconds)

        await self._close_all_positions()
        return await self._build_result()

    def _handle_sigint(self) -> None:
        """Set the shutdown flag for graceful exit on SIGINT."""
        self._shutdown = True

    def _check_loss_limit(self) -> bool:
        """Check whether the portfolio has breached the loss limit.

        Returns:
            ``True`` if total equity has dropped below the allowed threshold.

        """
        if self._initial_balance <= ZERO:
            return False
        equity = self._portfolio.total_equity
        return equity / self._initial_balance < (Decimal(1) - self._max_loss_pct)

    def _compute_sleep(self) -> float:
        """Compute seconds to sleep before the next tick.

        When end-time overrides are available, sleep until the snipe window
        opens (minus a small buffer) instead of polling at the default
        interval.  Inside the snipe window, fast-poll at
        ``snipe_poll_seconds``.  Fall back to ``poll_interval_seconds``
        when no end times are configured.

        Returns:
            Number of seconds to sleep before the next polling cycle.

        """
        if not self._end_time_overrides:
            return float(self._config.poll_interval_seconds)

        now = time.time()
        earliest_end: float | None = None
        for end_iso in self._end_time_overrides.values():
            try:
                end_dt = datetime.fromisoformat(end_iso)
                if end_dt.tzinfo is None:
                    end_dt = end_dt.replace(tzinfo=UTC)
                end_ts = end_dt.timestamp()
            except (ValueError, OSError):
                continue
            if earliest_end is None or end_ts < earliest_end:
                earliest_end = end_ts

        if earliest_end is None:
            return float(self._config.poll_interval_seconds)

        seconds_remaining = earliest_end - now
        snipe_window = self._config.snipe_window_seconds

        if seconds_remaining > snipe_window + _SLEEP_BUFFER_SECONDS:
            sleep = seconds_remaining - snipe_window - _SLEEP_BUFFER_SECONDS
            logger.info(
                "Sleeping %.0fs until snipe window (%.0fs remaining)",
                sleep,
                seconds_remaining,
            )
            return sleep

        return float(self._config.snipe_poll_seconds)

    def _log_performance(self) -> None:
        """Log performance metrics at market rotation boundaries.

        Emit an INFO log line with equity, cash balance, position count, trade
        count, and return percentage so that long-running bots can be monitored
        via log files or CloudWatch without stopping the engine.
        """
        equity = self._portfolio.total_equity
        cash = self._portfolio.balance
        positions = len(self._portfolio.positions)
        trades = len(self._portfolio.trades)
        ret = (
            (equity - self._initial_balance) / self._initial_balance * 100
            if self._initial_balance > ZERO
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
        if ret <= Decimal(-20):
            logger.warning("DRAWDOWN ALERT return=%+.2f%%", ret)

    async def _fetch_snapshot(self, condition_id: str) -> MarketSnapshot | None:
        """Fetch market data and build a MarketSnapshot.

        Cache token IDs from the market response for order placement.

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

        if len(market.tokens) < _MIN_TOKENS:
            logger.warning("Market %s has fewer than 2 tokens", condition_id)
            return None
        yes_token = market.tokens[0]
        no_token = market.tokens[1]

        # Cache token IDs for order placement
        self._token_ids[condition_id] = (yes_token.token_id, no_token.token_id)

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

        Attempt to redeem resolved positions (if auto-redeem is enabled),
        then clear all local position tracking and refresh the balance.
        """
        if self._auto_redeem:
            await self._redeem_resolved()

        remaining = len(self._portfolio.positions)
        if remaining > 0:
            self._portfolio.clear_positions()
        self._position_outcomes.clear()
        await self._portfolio.refresh_balance()
        logger.info(
            "ROTATION: balance now $%.4f",
            self._portfolio.balance,
        )

        try:
            discovered = await self._client.discover_series_markets(
                list(self._config.series_slugs),
            )
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
        self._log_performance()

    async def _redeem_resolved(self) -> None:
        """Discover redeemable positions and kick off background CTF redemption.

        Query the Polymarket Data API for all redeemable positions held by
        the proxy wallet, extract condition IDs (skipping positions below
        the minimum order size), and spawn a background task to call
        ``client.redeem_positions()`` for on-chain CTF redemption at $1.00
        face value.

        The on-chain call runs in the background so the trading loop is not
        blocked by slow Polygon transactions.  Require POL in the signing
        EOA for gas.
        """
        if self._redeem_task is not None and not self._redeem_task.done():
            logger.info("AUTO-REDEEM: previous redemption still in progress, skipping")
            return

        try:
            redeemable = await self._client.get_redeemable_positions()
        except Exception:
            logger.warning("Failed to discover redeemable positions", exc_info=True)
            return

        if not redeemable:
            return

        logger.info("AUTO-REDEEM: found %d redeemable positions", len(redeemable))
        condition_ids: list[str] = []
        for pos in redeemable:
            if pos.size < _MIN_ORDER_SIZE:
                logger.info(
                    "REDEEM skip %s: size=%s below minimum %s",
                    pos.title[:40],
                    pos.size,
                    _MIN_ORDER_SIZE,
                )
                continue
            condition_ids.append(pos.condition_id)

        if not condition_ids:
            return

        self._redeem_task = asyncio.create_task(self._redeem_on_chain(condition_ids))

    async def _redeem_on_chain(self, condition_ids: list[str]) -> None:
        """Execute on-chain CTF redemption in the background.

        Log results when complete.  Errors are caught and logged so they
        do not propagate to the main trading loop.

        Args:
            condition_ids: Resolved market condition IDs to redeem.

        """
        try:
            redeemed = await self._client.redeem_positions(condition_ids)
            logger.info(
                "AUTO-REDEEM: redeemed %d/%d positions on-chain via CTF",
                redeemed,
                len(condition_ids),
            )
        except Exception:
            logger.warning("CTF redemption failed", exc_info=True)

    async def _tick(self) -> None:
        """Execute one polling cycle across all tracked markets."""
        if self._config.series_slugs:
            now = int(time.time())
            new_window = (now // _FIVE_MINUTES) * _FIVE_MINUTES
            if new_window != self._current_window:
                self._current_window = new_window
                await self._rotate_markets()

        await self._portfolio.refresh_balance()

        for condition_id in self._active_markets:
            if condition_id in self._position_outcomes:
                continue

            snapshot = await self._fetch_snapshot(condition_id)
            if snapshot is None:
                continue

            self._snapshots_processed += 1
            market_history = self._history.setdefault(
                condition_id,
                deque(maxlen=self._config.max_history),
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

            sig = self._strategy.on_snapshot(snapshot, history)
            if sig is not None:
                logger.info(
                    "[tick %d] SIGNAL: %s %s strength=%.4f reason=%s",
                    self._snapshots_processed,
                    sig.side.name,
                    sig.symbol[:20],
                    sig.strength,
                    sig.reason,
                )
                await self._apply_signal(sig, snapshot)
            else:
                logger.info("[tick %d] No signal", self._snapshots_processed)

            outcome = self._position_outcomes.get(condition_id, "Yes")
            mtm_price = snapshot.yes_price if outcome == "Yes" else snapshot.no_price
            self._portfolio.mark_to_market(condition_id, mtm_price)

    async def _apply_signal(self, sig: Signal, snapshot: MarketSnapshot) -> None:
        """Convert a strategy signal into a real trade.

        Size the position using the Kelly criterion and execute via the
        live portfolio. A SELL signal on a market with no open position
        is interpreted as "buy the NO/Down token" (the complement outcome).

        Args:
            sig: Trading signal from the strategy.
            snapshot: Current market snapshot.

        """
        condition_id = snapshot.condition_id
        token_ids = self._token_ids.get(condition_id)
        if token_ids is None:
            logger.warning("No cached token IDs for %s, skipping signal", condition_id[:20])
            return

        # Close existing position
        if sig.side == Side.SELL and condition_id in self._portfolio.positions:
            outcome = self._position_outcomes.get(condition_id, "Yes")
            close_price = snapshot.yes_price if outcome == "Yes" else snapshot.no_price
            token_id = token_ids[0] if outcome == "Yes" else token_ids[1]
            pos = self._portfolio.positions[condition_id]
            trade = await self._portfolio.close_position(
                condition_id,
                token_id,
                close_price,
                pos.quantity,
                snapshot.timestamp,
            )
            if trade is not None:
                logger.info(
                    "[tick %d] POSITION CLOSED: %s @ %.4f order=%s filled=%s",
                    self._snapshots_processed,
                    condition_id[:20],
                    close_price,
                    trade.order_id,
                    trade.filled,
                )
            self._position_outcomes.pop(condition_id, None)
            return

        # Open new position
        if condition_id not in self._portfolio.positions:
            if sig.side == Side.BUY:
                buy_price = snapshot.yes_price
                outcome = "Yes"
                token_id = token_ids[0]
            elif sig.side == Side.SELL:
                buy_price = snapshot.no_price
                outcome = "No"
                token_id = token_ids[1]
            else:
                return

            await self._open_position(
                condition_id,
                token_id,
                outcome,
                buy_price,
                snapshot,
                sig,
            )

    async def _open_position(
        self,
        condition_id: str,
        token_id: str,
        outcome: str,
        buy_price: Decimal,
        snapshot: MarketSnapshot,
        sig: Signal,
    ) -> None:
        """Open a new live position on the given outcome token.

        Size the position with Kelly criterion, refresh balance, and
        place the order via the live portfolio.

        Args:
            condition_id: Market condition identifier.
            token_id: CLOB token identifier for the outcome.
            outcome: Token outcome ("Yes" or "No").
            buy_price: Price at which to buy the token.
            snapshot: Current market snapshot.
            sig: Strategy signal that triggered the trade.

        """
        min_edge = Decimal("0.005")
        estimated_prob = max(
            buy_price + sig.strength * (Decimal(1) - buy_price),
            buy_price + min_edge,
        )
        estimated_prob = min(estimated_prob, Decimal("0.999"))

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
        logger.info(
            "[tick %d] Placing order: %s %s qty=%s @ %.4f kelly=%.4f balance=$%.4f",
            self._snapshots_processed,
            outcome,
            condition_id[:20],
            quantity,
            buy_price,
            fraction,
            self._portfolio.balance,
        )
        trade = await self._portfolio.open_position(
            condition_id=condition_id,
            token_id=token_id,
            outcome=outcome,
            side=Side.BUY,
            price=buy_price,
            quantity=quantity,
            timestamp=snapshot.timestamp,
            reason=sig.reason,
            edge=edge,
        )
        if trade is not None:
            logger.info(
                "[tick %d] TRADE OPENED: %s %s qty=%s @ %.4f edge=%.4f order=%s filled=%s",
                self._snapshots_processed,
                outcome,
                condition_id[:20],
                quantity,
                buy_price,
                edge,
                trade.order_id,
                trade.filled,
            )
            self._position_outcomes[condition_id] = outcome
        else:
            logger.warning(
                "[tick %d] TRADE REJECTED: %s (duplicate, insufficient balance, or API error)",
                self._snapshots_processed,
                condition_id[:20],
            )

    async def _close_all_positions(self) -> None:
        """Clear all position tracking before engine shutdown.

        For resolved 5-minute markets, Polymarket auto-redeems winning
        tokens so no SELL orders are needed.  Just clear local tracking.
        """
        open_count = len(self._portfolio.positions)
        if open_count > 0:
            self._portfolio.clear_positions()
            self._position_outcomes.clear()
            logger.info("SHUTDOWN: cleared %d positions", open_count)

    async def _build_result(self) -> LiveTradingResult:
        """Build the final result from the portfolio state.

        Returns:
            Summary of the live trading run.

        """
        trades = self._portfolio.trades
        final_balance = await self._portfolio.refresh_balance()

        metrics: dict[str, Decimal] = {}
        if trades:
            buy_trades = [t for t in trades if t.side == Side.BUY]
            sell_trades = [t for t in trades if t.side == Side.SELL]
            metrics["total_trades"] = Decimal(len(trades))
            metrics["buy_trades"] = Decimal(len(buy_trades))
            metrics["sell_trades"] = Decimal(len(sell_trades))
            metrics["total_return"] = (
                (final_balance - self._initial_balance) / self._initial_balance
                if self._initial_balance > ZERO
                else ZERO
            )

        return LiveTradingResult(
            strategy_name=self._strategy.name,
            initial_balance=self._initial_balance,
            final_balance=final_balance,
            trades=tuple(trades),
            snapshots_processed=self._snapshots_processed,
            metrics=metrics,
        )
