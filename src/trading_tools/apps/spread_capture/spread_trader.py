"""Core spread capture trading engine.

Run a polling loop that scans for spread opportunities on Polymarket
Up/Down markets, enters both sides simultaneously when the combined
cost is below the threshold, manages partial fills, and settles
positions at market expiry.

Guaranteed profit: when both sides are filled at a combined cost below
$1.00, the winning side pays $1.00 per token at settlement, netting
``quantity * (1.0 - combined)`` profit regardless of outcome.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING

import httpx

from trading_tools.apps.bot_framework.balance_manager import BalanceManager
from trading_tools.apps.bot_framework.heartbeat import HeartbeatLogger
from trading_tools.apps.bot_framework.order_executor import OrderExecutor
from trading_tools.apps.bot_framework.redeemer import PositionRedeemer
from trading_tools.apps.bot_framework.shutdown import GracefulShutdown
from trading_tools.apps.polymarket.backtest_common import compute_order_book_slippage
from trading_tools.apps.spread_capture.fees import compute_poly_fee
from trading_tools.clients.binance.client import BinanceClient
from trading_tools.clients.binance.exceptions import BinanceError
from trading_tools.clients.polymarket.models import OrderBook
from trading_tools.core.models import ONE, ZERO, Interval, Side
from trading_tools.data.providers.binance import BinanceCandleProvider
from trading_tools.data.providers.order_book_feed import OrderBookFeed

from .market_scanner import MarketScanner
from .models import (
    PairedPosition,
    PositionState,
    SideLeg,
    SpreadOpportunity,
    SpreadResult,
    SpreadResultRecord,
)

if TYPE_CHECKING:
    from trading_tools.clients.polymarket.client import PolymarketClient

    from .config import SpreadCaptureConfig
    from .repository import SpreadResultRepository

logger = logging.getLogger(__name__)

_BALANCE_REFRESH_POLLS = 60
_WIN_PRICE = Decimal("1.0")
_MIN_TOKEN_QTY = Decimal(5)
_SUMMARY_INTERVAL = 900  # 15 minutes
_SINGLE_LEG_MIN_REMAINING = 60  # seconds remaining to attempt early exit


def _empty_position_dict() -> dict[str, PairedPosition]:
    """Return an empty dict for dataclass default_factory."""
    return {}


def _empty_str_set() -> set[str]:
    """Return an empty set for dataclass default_factory."""
    return set()


def _empty_result_list() -> list[SpreadResult]:
    """Return an empty list for dataclass default_factory."""
    return []


@dataclass
class SpreadTrader:
    """Spread capture trading engine for Polymarket Up/Down markets.

    Scan for markets where the combined cost of buying both sides is
    below the configured threshold, enter both sides simultaneously,
    and settle at market expiry for guaranteed profit.

    Attributes:
        config: Immutable service configuration.
        live: Enable live trading (requires ``client``).
        client: Authenticated Polymarket client for CLOB data and orders.

    """

    config: SpreadCaptureConfig
    live: bool = False
    client: PolymarketClient | None = None
    _scanner: MarketScanner | None = field(default=None, repr=False)
    _binance: BinanceClient | None = field(default=None, repr=False)
    _positions: dict[str, PairedPosition] = field(default_factory=_empty_position_dict, repr=False)
    _settled_cids: set[str] = field(default_factory=_empty_str_set, init=False, repr=False)
    _results: list[SpreadResult] = field(default_factory=_empty_result_list, repr=False)
    _poll_count: int = field(default=0, repr=False)
    _shutdown: GracefulShutdown = field(default_factory=GracefulShutdown, init=False, repr=False)
    _heartbeat: HeartbeatLogger = field(default_factory=HeartbeatLogger, init=False, repr=False)
    _summary_due: float = field(default=0.0, init=False, repr=False)
    _redeemer: PositionRedeemer | None = field(default=None, init=False, repr=False)
    _executor: OrderExecutor | None = field(default=None, init=False, repr=False)
    _balance_manager: BalanceManager | None = field(default=None, init=False, repr=False)
    _repo: SpreadResultRepository | None = field(default=None, init=False, repr=False)
    _consecutive_losses: int = field(default=0, init=False, repr=False)
    _circuit_breaker_until: int = field(default=0, init=False, repr=False)
    _session_start_capital: Decimal = field(default=ZERO, init=False, repr=False)
    _high_water_mark: Decimal = field(default=ZERO, init=False, repr=False)
    _book_feed: OrderBookFeed | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        """Initialize shared services when running in live mode."""
        if self.live and self.client is not None:
            # Maker strategy gets partial fills — lower redeem minimum
            redeem_min = Decimal(1) if self.config.strategy == "maker" else _MIN_TOKEN_QTY
            self._redeemer = PositionRedeemer(client=self.client, min_order_size=redeem_min)
            self._executor = OrderExecutor(
                client=self.client,
                use_market_orders=self.config.use_market_orders,
            )
            self._balance_manager = BalanceManager(client=self.client)

    async def run(self) -> None:
        """Run the polling loop until interrupted.

        Initialize the market scanner and enter a tight async loop that
        scans for opportunities, enters spread positions, and settles
        expired ones.  Log a heartbeat every 60 seconds for monitoring.
        """
        if self.client is None:
            msg = "PolymarketClient is required — pass client at construction"
            raise RuntimeError(msg)

        # Maker strategy bids at fixed prices, so accept all markets.
        # Use 2.0 (not 1.0) because best asks often sum to > $1.00.
        scanner_max_cost = (
            Decimal("2.0") if self.config.strategy == "maker" else self.config.max_combined_cost
        )
        # Net margin is negative for typical markets (combined > 1.0),
        # so use a very negative floor to pass all markets through.
        scanner_min_margin = (
            Decimal(-10) if self.config.strategy == "maker" else self.config.min_spread_margin
        )
        self._scanner = MarketScanner(
            client=self.client,
            series_slugs=self.config.series_slugs,
            max_combined_cost=scanner_max_cost,
            min_spread_margin=scanner_min_margin,
            max_window_seconds=self.config.max_window_seconds,
            max_entry_age_pct=self.config.max_entry_age_pct,
            rediscovery_interval=self.config.rediscovery_interval,
            fee_rate=self.config.fee_rate,
            fee_exponent=self.config.fee_exponent,
        )
        self._binance = BinanceClient()
        self._shutdown.install()

        # Start WebSocket order book feed for maker strategy
        if self.config.strategy == "maker":
            self._book_feed = OrderBookFeed()
            await self._book_feed.start([])

        if self.live and self._balance_manager is not None:
            await self._balance_manager.refresh()

        mode = "LIVE" if self.live else "PAPER"
        capital = self._get_capital()
        self._session_start_capital = capital
        self._high_water_mark = capital
        logger.info(
            "spread-capture started mode=%s poll=%ds capital=$%s"
            " max_pos=%s%% max_combined=%.4f min_margin=%.4f"
            " max_open=%d slugs=%s",
            mode,
            self.config.poll_interval,
            capital,
            self.config.max_position_pct * 100,
            self.config.max_combined_cost,
            self.config.min_spread_margin,
            self.config.max_open_positions,
            ",".join(self.config.series_slugs),
        )

        try:
            while not self._shutdown.should_stop:
                await self._poll_cycle()
                self._log_heartbeat()
                now = time.monotonic()
                if now >= self._summary_due:
                    self._log_periodic_summary()
                    self._summary_due = now + _SUMMARY_INTERVAL
                await asyncio.sleep(self.config.poll_interval)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await self._safe_shutdown()
            self._log_summary()
            if self._book_feed is not None:
                await self._book_feed.stop()
            if self._binance is not None:  # pyright: ignore[reportUnnecessaryComparison]
                await self._binance.close()

    async def _safe_shutdown(self) -> None:
        """Cancel all open GTC orders on the CLOB before exiting.

        Prevent orphaned resting orders from accumulating fills without
        hedge or take-profit protection.  For live maker positions, also
        attempt to sell any single-filled legs at market to recover capital.
        """
        if not self.live or self.client is None:
            return

        logger.info("SAFE SHUTDOWN: cancelling all open orders...")
        try:
            open_orders = await self.client.get_open_orders()
            for order in open_orders:
                await self._cancel_order_safe(order.order_id)
            logger.info("SAFE SHUTDOWN: cancelled %d open orders", len(open_orders))
        except (httpx.HTTPError, Exception):
            logger.warning("SAFE SHUTDOWN: failed to cancel open orders")

        # Sell any single-filled legs to recover capital
        single_filled = [
            (cid, pos)
            for cid, pos in self._positions.items()
            if pos.state == PositionState.PENDING
            and not pos.is_paper
            and (pos.pending_up_order_id is None) != (pos.pending_down_order_id is None)
        ]
        for cid, pos in single_filled:
            up_filled = pos.pending_up_order_id is None
            filled_side = "Up" if up_filled else "Down"
            token_id = pos.opportunity.up_token_id if up_filled else pos.opportunity.down_token_id
            filled_leg = pos.up_leg if up_filled else pos.down_leg
            if filled_leg is None:
                continue
            pnl = await self._unwind_filled_leg(token_id, filled_leg)
            logger.info(
                "SAFE SHUTDOWN: unwound %s %s pnl=%.4f asset=%s",
                cid[:12],
                filled_side,
                pnl,
                pos.opportunity.asset,
            )

    def set_repo(self, repo: SpreadResultRepository) -> None:
        """Attach a database repository for persisting settled trade results.

        Args:
            repo: An initialised ``SpreadResultRepository`` instance.

        """
        self._repo = repo

    def stop(self) -> None:
        """Signal the polling loop to stop after the current cycle."""
        self._shutdown.request()

    @property
    def _committed_capital(self) -> Decimal:
        """Return the total cost basis of all open positions."""
        return sum(
            (pos.total_cost_basis for pos in self._positions.values()),
            start=ZERO,
        )

    def _get_capital(self) -> Decimal:
        """Return the current capital available for position sizing.

        In live mode, use the real USDC balance from the shared
        ``BalanceManager``.  In paper mode, start from base capital,
        optionally add realised P&L, and subtract committed capital.

        Returns:
            Available capital in USDC.

        """
        if self._balance_manager is not None and self._balance_manager.balance > ZERO:
            return self._balance_manager.balance

        base = self.config.capital
        if self.config.compound_profits:
            base += sum((r.pnl for r in self._results), start=ZERO)
        return base - self._committed_capital

    def _total_capital(self) -> Decimal:
        """Return the total capital for exposure-limit denominators.

        Returns:
            Total capital in USDC (available + committed).

        """
        if self._balance_manager is not None and self._balance_manager.balance > ZERO:
            return self._balance_manager.balance + self._committed_capital

        base = self.config.capital
        if self.config.compound_profits:
            base += sum((r.pnl for r in self._results), start=ZERO)
        return base

    async def _poll_cycle(self) -> None:
        """Execute one scan-enter-settle cycle.

        Refresh balance periodically, scan for opportunities, enter
        new spread positions, manage single-leg positions, and settle
        expired ones.
        """
        if self._scanner is None:
            msg = "MarketScanner not initialised — call run() first"
            raise RuntimeError(msg)
        self._poll_count += 1

        if (
            self.live
            and self._balance_manager is not None
            and self._poll_count % _BALANCE_REFRESH_POLLS == 0
        ):
            await self._balance_manager.refresh()

        # Manage pending GTC limit orders before scanning for new opportunities
        await self._manage_pending_orders()

        # Attempt early exit for single-leg positions
        await self._manage_single_leg_positions()

        exclude_cids = set(self._positions.keys()) | self._settled_cids
        opportunities = await self._scanner.scan(exclude_cids)

        for opp in opportunities:
            if len(self._positions) >= self.config.max_open_positions:
                break
            await self._enter_spread(opp)

        await self._settle_expired_positions()

        # Update WebSocket subscriptions based on active positions
        await self._sync_book_feed_subscriptions()

        if self._redeemer is not None:
            await self._redeemer.redeem_if_available()

    async def _sync_book_feed_subscriptions(self) -> None:
        """Update WebSocket book feed subscriptions to match active positions.

        Collect all token IDs from open positions and update the book feed
        subscription.  Only active for the maker strategy.
        """
        if self._book_feed is None:
            return
        token_ids: list[str] = []
        for pos in self._positions.values():
            token_ids.append(pos.opportunity.up_token_id)
            token_ids.append(pos.opportunity.down_token_id)
        # Deduplicate while preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for tid in token_ids:
            if tid not in seen:
                seen.add(tid)
                unique.append(tid)
        if sorted(unique) != sorted(self._book_feed.subscribed_tokens):
            await self._book_feed.update_subscription(unique)

    async def _validate_and_reprice(
        self, opp: SpreadOpportunity, qty: Decimal
    ) -> tuple[Decimal, Decimal, Decimal] | None:
        """Re-fetch order books and compute VWAP prices for live entry.

        Guard against stale scanner data by re-fetching both order books,
        applying market impact cap (limit qty to ``max_book_pct`` of
        visible depth), computing VWAP fill prices, and re-checking
        combined cost and net margin thresholds.

        Args:
            opp: The spread opportunity from the scanner.
            qty: Desired token quantity.

        Returns:
            Tuple of ``(up_vwap, down_vwap, capped_qty)`` if the opportunity
            is still viable, or ``None`` if validation fails.

        """
        if self.client is None:
            return None

        up_book, down_book = await asyncio.gather(
            self.client.get_order_book(opp.up_token_id),
            self.client.get_order_book(opp.down_token_id),
        )

        # Market impact cap: limit qty to max_book_pct of visible depth
        up_depth = sum((level.size for level in up_book.asks), start=ZERO)
        down_depth = sum((level.size for level in down_book.asks), start=ZERO)
        max_qty = min(up_depth, down_depth) * self.config.max_book_pct
        qty = min(qty, max_qty.quantize(Decimal("0.01")))
        if qty < _MIN_TOKEN_QTY:
            logger.debug("Skipping: qty capped below minimum by depth")
            return None

        # VWAP pricing: walk the order book for actual fill price
        up_vwap = compute_order_book_slippage(up_book, Side.BUY, ONE, qty)
        down_vwap = compute_order_book_slippage(down_book, Side.BUY, ONE, qty)
        if up_vwap is None or down_vwap is None:
            logger.debug("Skipping: insufficient book depth for VWAP")
            return None

        # Re-check combined cost and net margin
        combined = up_vwap + down_vwap
        up_fee = compute_poly_fee(up_vwap, self.config.fee_rate, self.config.fee_exponent)
        down_fee = compute_poly_fee(down_vwap, self.config.fee_rate, self.config.fee_exponent)
        net_margin = ONE - combined - up_fee - down_fee

        if combined >= self.config.max_combined_cost:
            logger.info("STALE: combined=%.4f >= threshold after re-fetch", combined)
            return None
        if net_margin < self.config.min_spread_margin:
            logger.info("STALE: net_margin=%.4f < min after re-fetch", net_margin)
            return None

        return up_vwap, down_vwap, qty

    async def _unwind_filled_leg(self, token_id: str, leg: SideLeg) -> Decimal:
        """Sell a filled leg at market to unwind a failed spread.

        Place a market sell order for the filled leg and return the
        realised P&L (usually a small loss from the spread).

        Args:
            token_id: CLOB token ID for the filled leg.
            leg: The filled side leg to sell.

        Returns:
            Realised P&L from the unwind.  Returns zero if the sell
            order failed or was not placed.

        """
        if self._executor is None:
            return ZERO
        try:
            resp = await self._executor.place_order(token_id, "SELL", leg.entry_price, leg.quantity)
            if resp is not None and resp.filled > ZERO:
                sell_proceeds = resp.price * resp.filled
                return sell_proceeds - leg.cost_basis
        except (httpx.HTTPError, Exception):
            logger.debug("Failed to unwind %s leg", leg.side)
        return ZERO

    async def _manage_single_leg_positions(self) -> None:
        """Attempt early exit for SINGLE_LEG positions with time remaining.

        For live mode, sell the filled leg at market when there is enough
        time remaining before expiry.  This recovers most of the capital
        rather than waiting for settlement (which is a coin flip).
        """
        if not self.live or self._executor is None:
            return

        now = int(time.time())
        single_legs = [
            (cid, pos)
            for cid, pos in self._positions.items()
            if pos.state == PositionState.SINGLE_LEG
            and pos.opportunity.window_end_ts - now > _SINGLE_LEG_MIN_REMAINING
        ]

        for cid, pos in single_legs:
            token_id = pos.opportunity.up_token_id
            pnl = await self._unwind_filled_leg(token_id, pos.up_leg)
            if pnl != ZERO:
                result = SpreadResult(
                    opportunity=pos.opportunity,
                    state=PositionState.SETTLED,
                    up_entry=pos.up_leg.entry_price,
                    up_qty=pos.up_leg.quantity,
                    down_entry=None,
                    down_qty=None,
                    total_cost_basis=pos.up_leg.cost_basis,
                    entry_time=pos.entry_time,
                    exit_time=now,
                    winning_side=None,
                    pnl=pnl,
                    is_paper=False,
                    order_ids=tuple(pos.up_leg.order_ids),
                    outcome_known=True,
                )
                self._results.append(result)
                await self._persist_result(result)
                self._settled_cids.add(cid)
                del self._positions[cid]
                logger.info(
                    "LIVE UNWIND %s single-leg early exit pnl=%.4f",
                    cid[:12],
                    pnl,
                )

    async def _enter_maker_position(self, opp: SpreadOpportunity, now: int) -> None:
        """Place resting GTC limit buy orders at fixed bid prices on both sides.

        Post maker bids at configured prices (``maker_bid_up``,
        ``maker_bid_down``) and wait for taker sells to fill them.
        Position starts as PENDING and transitions to PAIRED when both
        sides fill via ``_manage_pending_orders()``.

        Args:
            opp: The spread opportunity (market) to enter.
            now: Current epoch seconds.

        """
        bid_up = self.config.maker_bid_up
        bid_down = self.config.maker_bid_down
        combined = bid_up + bid_down

        # Scale order size with capital, floored by maker_order_size minimum
        capital = self._get_capital()
        spend = capital * self.config.max_position_pct
        scaled_qty = (spend / combined).quantize(Decimal("0.01"))
        qty = max(scaled_qty, self.config.maker_order_size)
        qty = max(qty, _MIN_TOKEN_QTY)
        if combined >= ONE:
            logger.warning("SKIP %s: combined bid %.4f >= 1.00", opp.condition_id[:12], combined)
            return

        up_leg = SideLeg(side="Up", entry_price=bid_up, quantity=qty, cost_basis=bid_up * qty)
        down_leg = SideLeg(
            side="Down", entry_price=bid_down, quantity=qty, cost_basis=bid_down * qty
        )

        if self.live:
            result = await self._place_spread_orders(opp, up_leg, down_leg)
            up_result, down_result = result
            if up_result is None and down_result is None:
                # Cancel any orders that may have been placed despite the error
                await self._cancel_orders_for_market(opp)
                logger.warning("Both maker orders failed for %s", opp.condition_id[:12])
                return

            pos = PairedPosition(
                opportunity=opp,
                state=PositionState.PENDING,
                up_leg=up_result or up_leg,
                down_leg=down_result or down_leg,
                entry_time=now,
                is_paper=False,
                pending_up_order_id=(up_result.order_ids[-1] if up_result else None),
                pending_down_order_id=(down_result.order_ids[-1] if down_result else None),
            )
        else:
            # Paper mode: position stays PENDING, fills simulated by
            # checking if market best ask <= our bid in _manage_pending_orders()
            pos = PairedPosition(
                opportunity=opp,
                state=PositionState.PENDING,
                up_leg=up_leg,
                down_leg=down_leg,
                entry_time=now,
                is_paper=True,
                pending_up_order_id="paper_up",
                pending_down_order_id="paper_down",
            )

        self._positions[opp.condition_id] = pos
        mode = "LIVE" if self.live else "PAPER"
        logger.info(
            "%s MAKER %s bid_up=%.4f bid_down=%.4f combined=%.4f margin=%.4f qty=%.0f asset=%s",
            mode,
            opp.condition_id[:12],
            bid_up,
            bid_down,
            combined,
            ONE - combined,
            qty,
            opp.asset,
        )

    async def _enter_spread(self, opp: SpreadOpportunity) -> None:
        """Enter a spread position by buying both sides simultaneously.

        Skip if capital is insufficient, drawdown halt is active, or
        circuit breaker is cooling down.  In live mode, re-validate
        prices via VWAP before placing orders.

        Args:
            opp: The spread opportunity to enter.

        """
        now = int(time.time())

        if self.config.strategy == "maker":
            await self._enter_maker_position(opp, now)
            return

        # Max drawdown kill-switch
        if self._check_drawdown_halt():
            logger.info("  DRAWDOWN-HALT active, skipping %s", opp.condition_id[:12])
            return

        if self._circuit_breaker_until > now:
            logger.info(
                "  CIRCUIT-BREAKER active for %ds, skipping %s",
                self._circuit_breaker_until - now,
                opp.condition_id[:12],
            )
            return

        capital = self._get_capital()
        spend = capital * self.config.max_position_pct
        # Quantity is spend divided by combined cost (buying one of each side)
        qty = (spend / opp.combined).quantize(Decimal("0.01"))

        if qty < _MIN_TOKEN_QTY:
            logger.debug(
                "Skipping %s: quantity %.2f below minimum",
                opp.condition_id[:12],
                qty,
            )
            return

        # Live mode: re-validate prices via VWAP and apply market impact cap
        up_price = opp.up_price
        down_price = opp.down_price
        if self.live:
            repriced = await self._validate_and_reprice(opp, qty)
            if repriced is None:
                return
            up_price, down_price, qty = repriced
        elif self.config.paper_slippage_pct > ZERO:
            # Apply paper slippage: worsen both prices
            up_price = up_price * (ONE + self.config.paper_slippage_pct)
            down_price = down_price * (ONE + self.config.paper_slippage_pct)

        up_cost = up_price * qty
        down_cost = down_price * qty

        up_leg = SideLeg(
            side="Up",
            entry_price=up_price,
            quantity=qty,
            cost_basis=up_cost,
        )
        down_leg = SideLeg(
            side="Down",
            entry_price=down_price,
            quantity=qty,
            cost_basis=down_cost,
        )

        # Place live orders or record paper fills
        if self.live:
            live_result = await self._handle_live_entry(opp, up_leg, down_leg, qty, now)
            if live_result is None:
                return
            up_leg, down_leg = live_result

        pos = PairedPosition(
            opportunity=opp,
            state=PositionState.PAIRED,
            up_leg=up_leg,
            down_leg=down_leg,
            entry_time=now,
            is_paper=not self.live,
        )
        self._positions[opp.condition_id] = pos

        combined_actual = up_price + down_price
        mode = "LIVE" if self.live else "PAPER"
        logger.info(
            "%s OPEN %s up=%.4f down=%.4f combined=%.4f margin=%.4f qty=%.2f cost=$%.4f asset=%s",
            mode,
            opp.condition_id[:12],
            up_price,
            down_price,
            combined_actual,
            ONE - combined_actual,
            qty,
            up_cost + down_cost,
            opp.asset,
        )

    async def _handle_live_entry(
        self,
        opp: SpreadOpportunity,
        up_leg: SideLeg,
        down_leg: SideLeg,
        qty: Decimal,
        now: int,
    ) -> tuple[SideLeg, SideLeg] | None:
        """Place live orders and handle partial fills.

        Return the final (up_leg, down_leg) if both sides are filled
        (FOK) or a PENDING position is created (GTC).  Return ``None``
        if the caller should abort entry.

        Args:
            opp: The spread opportunity being entered.
            up_leg: Pre-computed Up leg.
            down_leg: Pre-computed Down leg.
            qty: Token quantity.
            now: Current epoch seconds.

        Returns:
            Tuple of filled legs, or ``None`` to abort.

        """
        result = await self._place_spread_orders(opp, up_leg, down_leg)
        up_leg_result, down_leg_result = result
        if up_leg_result is None and down_leg_result is None:
            await self._cancel_orders_for_market(opp)
            logger.warning("Both leg orders failed for %s", opp.condition_id[:12])
            return None

        # With GTC limit orders, create PENDING position and track order IDs
        if not self.config.use_market_orders:
            pos = PairedPosition(
                opportunity=opp,
                state=PositionState.PENDING,
                up_leg=up_leg_result if up_leg_result is not None else up_leg,
                down_leg=down_leg_result if down_leg_result is not None else down_leg,
                entry_time=now,
                is_paper=False,
                pending_up_order_id=(up_leg_result.order_ids[-1] if up_leg_result else None),
                pending_down_order_id=(down_leg_result.order_ids[-1] if down_leg_result else None),
            )
            self._positions[opp.condition_id] = pos
            logger.info(
                "LIVE PENDING %s up_oid=%s down_oid=%s qty=%.2f asset=%s",
                opp.condition_id[:12],
                pos.pending_up_order_id,
                pos.pending_down_order_id,
                qty,
                opp.asset,
            )
            return None

        # FOK market order path: immediate fill or fail
        if up_leg_result is None:
            logger.warning("Up leg order failed for %s", opp.condition_id[:12])
            return None
        if down_leg_result is None:
            # Auto-unwind the filled Up leg
            unwind_pnl = await self._unwind_filled_leg(opp.up_token_id, up_leg_result)
            if unwind_pnl != ZERO:
                logger.info(
                    "LIVE UNWIND %s: Down failed, sold Up pnl=%.4f",
                    opp.condition_id[:12],
                    unwind_pnl,
                )
                return None
            # Unwind failed — fall through to SINGLE_LEG
            pos = PairedPosition(
                opportunity=opp,
                state=PositionState.SINGLE_LEG,
                up_leg=up_leg_result,
                down_leg=None,
                entry_time=now,
                is_paper=False,
            )
            self._positions[opp.condition_id] = pos
            logger.warning(
                "LIVE SINGLE-LEG %s: only Up filled, qty=%.2f",
                opp.condition_id[:12],
                up_leg_result.quantity,
            )
            return None
        return up_leg_result, down_leg_result

    async def _place_spread_orders(
        self,
        opp: SpreadOpportunity,
        up_leg: SideLeg,
        down_leg: SideLeg,
    ) -> tuple[SideLeg | None, SideLeg | None]:
        """Place concurrent buy orders for both sides of a spread.

        Args:
            opp: The spread opportunity being entered.
            up_leg: Pre-computed Up leg to order.
            down_leg: Pre-computed Down leg to order.

        Returns:
            Tuple of (up_leg, down_leg) with fill-adjusted values,
            or ``None`` for failed legs.

        """
        if self._executor is None:
            return None, None

        up_task = self._executor.place_order(
            opp.up_token_id, "BUY", up_leg.entry_price, up_leg.quantity
        )
        down_task = self._executor.place_order(
            opp.down_token_id, "BUY", down_leg.entry_price, down_leg.quantity
        )

        up_resp, down_resp = await asyncio.gather(up_task, down_task)

        result_up: SideLeg | None = None
        result_down: SideLeg | None = None

        if up_resp is not None:
            if up_resp.filled > ZERO:
                up_leg.quantity = up_resp.filled
                up_leg.cost_basis = up_resp.price * up_resp.filled
            up_leg.order_ids.append(up_resp.order_id)
            result_up = up_leg

        if down_resp is not None:
            if down_resp.filled > ZERO:
                down_leg.quantity = down_resp.filled
                down_leg.cost_basis = down_resp.price * down_resp.filled
            down_leg.order_ids.append(down_resp.order_id)
            result_down = down_leg

        return result_up, result_down

    def _get_order_books_from_feed(
        self, up_token_id: str, down_token_id: str
    ) -> tuple[OrderBook, OrderBook] | None:
        """Read order books from the WebSocket feed cache.

        Args:
            up_token_id: CLOB token ID for the Up outcome.
            down_token_id: CLOB token ID for the Down outcome.

        Returns:
            Tuple of ``(up_book, down_book)`` if both are cached, or
            ``None`` if either is missing.

        """
        if self._book_feed is None:
            return None
        up_book = self._book_feed.get_book(up_token_id)
        down_book = self._book_feed.get_book(down_token_id)
        if up_book is None or down_book is None:
            return None
        return up_book, down_book

    async def _fetch_books_for_position(
        self, pos: PairedPosition
    ) -> tuple[OrderBook, OrderBook] | None:
        """Fetch order books for both sides of a position.

        Prefer the WebSocket book feed cache, falling back to REST.

        Args:
            pos: The position with token IDs.

        Returns:
            Tuple of ``(up_book, down_book)`` or ``None`` if unavailable.

        """
        cached = self._get_order_books_from_feed(
            pos.opportunity.up_token_id, pos.opportunity.down_token_id
        )
        if cached is not None:
            return cached
        if self.client is None:
            return None
        try:
            up, down = await asyncio.gather(
                self.client.get_order_book(pos.opportunity.up_token_id),
                self.client.get_order_book(pos.opportunity.down_token_id),
            )
        except (httpx.HTTPError, Exception):
            logger.debug("Failed to fetch order books via REST fallback")
            return None
        else:
            return up, down

    async def _manage_paper_maker_orders(self) -> None:
        """Simulate fills for paper-mode maker positions using order book data.

        For each PENDING paper position in maker strategy, check whether the
        best ask is at or below our bid price.  Use the WebSocket book feed
        when available, falling back to REST for order book data.  If both
        sides fill, transition to PAIRED.  If the market expires with
        unfilled sides, remove the position.
        """
        if self.client is None and self._book_feed is None:
            return

        pending = [
            (cid, pos)
            for cid, pos in self._positions.items()
            if pos.state == PositionState.PENDING and pos.is_paper
        ]
        if not pending:
            return

        now = int(time.time())
        for cid, pos in pending:
            if pos.opportunity.window_end_ts <= now:
                self._settled_cids.add(cid)
                del self._positions[cid]
                logger.info("PAPER EXPIRED %s: market expired while pending", cid[:12])
                continue

            up_filled = pos.pending_up_order_id is None
            down_filled = pos.pending_down_order_id is None

            if up_filled and down_filled:
                continue

            result = await self._fetch_books_for_position(pos)
            if result is None:
                continue
            up_book, down_book = result

            if not up_filled and up_book.asks:
                best_ask = min(level.price for level in up_book.asks)
                if best_ask <= pos.up_leg.entry_price:
                    pos.pending_up_order_id = None
                    up_filled = True
                    logger.info(
                        "PAPER FILL %s Up: ask=%.4f <= bid=%.4f",
                        cid[:12],
                        best_ask,
                        pos.up_leg.entry_price,
                    )

            if not down_filled and pos.down_leg is not None and down_book.asks:
                best_ask = min(level.price for level in down_book.asks)
                if best_ask <= pos.down_leg.entry_price:
                    pos.pending_down_order_id = None
                    down_filled = True
                    logger.info(
                        "PAPER FILL %s Down: ask=%.4f <= bid=%.4f",
                        cid[:12],
                        best_ask,
                        pos.down_leg.entry_price,
                    )

            if up_filled and down_filled:
                pos.state = PositionState.PAIRED
                combined = pos.up_leg.entry_price + (
                    pos.down_leg.entry_price if pos.down_leg else ZERO
                )
                logger.info(
                    "PAPER PAIRED %s: both maker bids filled, combined=%.4f margin=%.4f asset=%s",
                    cid[:12],
                    combined,
                    ONE - combined,
                    pos.opportunity.asset,
                )

    async def _get_binance_direction(self, opp: SpreadOpportunity, now: int) -> str | None:
        """Determine the current price direction for a market's underlying asset.

        Fetch Binance 1-min candles from the window start to now and compare
        the opening price with the latest close.

        Args:
            opp: The spread opportunity with asset and window timestamps.
            now: Current epoch seconds.

        Returns:
            ``"Up"`` if price rose, ``"Down"`` if price fell, ``None`` if
            candle data is unavailable or price is flat.

        """
        if self._binance is None:
            return None
        try:
            provider = BinanceCandleProvider(self._binance)
            candles = await provider.get_candles(
                opp.asset,
                Interval.M1,
                opp.window_start_ts,
                now,
            )
        except (BinanceError, httpx.HTTPError, KeyError, ValueError):
            logger.debug("Failed to fetch Binance candles for hedge signal on %s", opp.asset)
            return None

        if not candles:
            return None

        open_price = candles[0].open
        close_price = candles[-1].close
        if close_price > open_price:
            return "Up"
        if close_price < open_price:
            return "Down"
        return None

    def _apply_hedge_to_position(
        self,
        pos: PairedPosition,
        unfilled_side: str,
        hedge_leg: SideLeg,
    ) -> None:
        """Apply a hedge fill to a pending position and transition to PAIRED.

        Replace the unfilled leg with the hedge leg, clear its pending
        order ID, and set the position state to PAIRED.

        Args:
            pos: The pending position to hedge.
            unfilled_side: ``"Up"`` or ``"Down"`` — which side was hedged.
            hedge_leg: The filled hedge leg.

        """
        if unfilled_side == "Up":
            pos.up_leg = hedge_leg
            pos.pending_up_order_id = None
        elif pos.down_leg is not None:
            pos.down_leg = hedge_leg
            pos.pending_down_order_id = None
        pos.state = PositionState.PAIRED

    async def _execute_live_hedge(
        self,
        cid: str,
        pos: PairedPosition,
        unfilled_token_id: str,
        unfilled_side: str,
        hedge_ask: Decimal,
        qty: Decimal,
    ) -> bool:
        """Place a live hedge order and apply the fill.

        Args:
            cid: Condition ID for logging.
            pos: The pending position.
            unfilled_token_id: CLOB token ID for the unfilled side.
            unfilled_side: ``"Up"`` or ``"Down"``.
            hedge_ask: Ask price for the hedge order.
            qty: Token quantity to buy.

        Returns:
            ``True`` if the hedge was executed successfully.

        """
        if self._executor is None:
            return False
        try:
            resp = await self._executor.place_order(unfilled_token_id, "BUY", hedge_ask, qty)
        except (httpx.HTTPError, Exception):
            logger.debug("Hedge order failed for %s %s", cid[:12], unfilled_side)
            return False

        if resp is None:
            return False

        # Always transition to PAIRED after placing a hedge order, even if
        # not immediately filled.  This prevents the hedge from firing again
        # on the next poll cycle and placing duplicate orders.
        hedge_leg = SideLeg(
            side=unfilled_side,
            entry_price=hedge_ask,
            quantity=qty,
            cost_basis=hedge_ask * qty,
            order_ids=[resp.order_id] if resp.order_id else [],
        )
        self._apply_hedge_to_position(pos, unfilled_side, hedge_leg)
        return True

    async def _maybe_hedge_maker_positions(self) -> None:
        """Hedge single-filled maker positions when the unfilled side is winning.

        For PENDING maker positions where one side has filled and enough of
        the window has elapsed (``maker_hedge_age_pct``), check Binance spot
        direction.  If the **unfilled** side is winning (meaning our filled
        side will lose), market-buy the unfilled side at the current ask to
        lock in guaranteed spread profit.

        Only hedge when the combined cost (filled bid + hedge ask) is below
        ``maker_max_hedge_combined``.
        """
        if self.client is None:
            return

        now = int(time.time())
        max_combined = self.config.maker_max_hedge_combined
        hedge_window = 90  # Start hedging in the last 90 seconds

        candidates = [
            (cid, pos)
            for cid, pos in self._positions.items()
            if pos.state == PositionState.PENDING
            and (pos.pending_up_order_id is None) != (pos.pending_down_order_id is None)
        ]

        for cid, pos in candidates:
            opp = pos.opportunity
            remaining = opp.window_end_ts - now
            if remaining > hedge_window:
                continue

            up_filled = pos.pending_up_order_id is None
            filled_side = "Up" if up_filled else "Down"
            unfilled_side = "Down" if up_filled else "Up"

            direction = await self._get_binance_direction(opp, now)
            hedge_ask = (
                await self._get_hedge_ask(cid, pos, unfilled_side)
                if direction == unfilled_side
                else None
            )
            if hedge_ask is None:
                continue

            filled_bid = (
                pos.up_leg.entry_price
                if up_filled
                else (pos.down_leg.entry_price if pos.down_leg else ZERO)
            )
            combined = filled_bid + hedge_ask

            # Linear scale: at 90s accept $0.98, at 0s accept $1.15.
            max_extra = Decimal("0.17")
            progress = (
                ONE - Decimal(str(remaining)) / Decimal(hedge_window) if remaining > 0 else ONE
            )
            effective_max = max_combined + max_extra * progress

            if combined >= effective_max:
                logger.debug(
                    "HEDGE SKIP %s: combined=%.4f >= max %.4f (remaining=%ds)",
                    cid[:12],
                    combined,
                    effective_max,
                    remaining,
                )
                continue

            qty = (
                pos.up_leg.quantity
                if up_filled
                else (pos.down_leg.quantity if pos.down_leg else ZERO)
            )

            if pos.is_paper:
                hedge_leg = SideLeg(
                    side=unfilled_side,
                    entry_price=hedge_ask,
                    quantity=qty,
                    cost_basis=hedge_ask * qty,
                )
                self._apply_hedge_to_position(pos, unfilled_side, hedge_leg)
            else:
                unfilled_token_id = (
                    opp.down_token_id if unfilled_side == "Down" else opp.up_token_id
                )
                if not await self._execute_live_hedge(
                    cid, pos, unfilled_token_id, unfilled_side, hedge_ask, qty
                ):
                    continue

            # Cancel all resting orders to prevent the filled side's GTC
            # from accumulating more fills after the hedge.
            if not pos.is_paper:
                await self._cancel_orders_for_market(opp)

            mode = "PAPER" if pos.is_paper else "LIVE"
            logger.info(
                "%s HEDGE %s %s at ask=%.4f combined=%.4f margin=%.4f"
                " direction=%s filled=%s asset=%s",
                mode,
                cid[:12],
                unfilled_side,
                hedge_ask,
                combined,
                ONE - combined,
                direction,
                filled_side,
                opp.asset,
            )

    async def _get_hedge_ask(
        self, cid: str, pos: PairedPosition, unfilled_side: str
    ) -> Decimal | None:
        """Fetch the best ask price for the unfilled side of a maker position.

        Prefer the WebSocket book feed cache, falling back to REST if the
        feed has no data for the token.

        Args:
            cid: Condition ID for logging.
            pos: The pending position.
            unfilled_side: ``"Up"`` or ``"Down"``.

        Returns:
            Best ask price, or ``None`` if order book is unavailable.

        """
        opp = pos.opportunity
        token_id = opp.down_token_id if unfilled_side == "Down" else opp.up_token_id

        # Prefer WebSocket book feed
        if self._book_feed is not None:
            book = self._book_feed.get_book(token_id)
            if book is not None and book.asks:
                return min(level.price for level in book.asks)

        # Fall back to REST
        if self.client is None:
            return None
        try:
            book = await self.client.get_order_book(token_id)
        except (httpx.HTTPError, Exception):
            logger.debug("Failed to fetch order book for hedge on %s", cid[:12])
            return None
        if not book.asks:
            return None
        return min(level.price for level in book.asks)

    async def _maybe_take_profit_maker(self) -> None:
        """Sell a single filled maker leg when the bid exceeds entry by the take-profit threshold.

        For PENDING maker positions where exactly one side has filled and
        the current best bid for that token is above the entry price by
        ``maker_take_profit_pct``, sell the filled leg immediately instead
        of waiting for settlement.  This locks in a guaranteed profit without
        the settlement coin-flip risk.
        """
        threshold = self.config.maker_take_profit_pct
        if threshold <= ZERO:
            return

        candidates = [
            (cid, pos)
            for cid, pos in self._positions.items()
            if pos.state == PositionState.PENDING
            and (pos.pending_up_order_id is None) != (pos.pending_down_order_id is None)
        ]

        for cid, pos in candidates:
            opp = pos.opportunity
            up_filled = pos.pending_up_order_id is None
            filled_side = "Up" if up_filled else "Down"
            filled_leg = pos.up_leg if up_filled else pos.down_leg
            if filled_leg is None:
                continue

            token_id = opp.up_token_id if up_filled else opp.down_token_id
            min_sell_price = filled_leg.entry_price * (ONE + threshold)

            # Check the bid price from WS feed or REST
            best_bid = self._get_best_bid(token_id)
            if best_bid is None or best_bid < min_sell_price:
                continue

            profit_per_token = best_bid - filled_leg.entry_price
            total_profit = profit_per_token * filled_leg.quantity

            if pos.is_paper:
                # Paper mode: simulate the sell
                pnl = total_profit
            elif self._executor is not None:
                # Live mode: sell at market
                try:
                    resp = await self._executor.place_order(
                        token_id, "SELL", best_bid, filled_leg.quantity
                    )
                except (httpx.HTTPError, Exception):
                    logger.debug("Take-profit sell failed for %s", cid[:12])
                    continue
                if resp is None or resp.filled <= ZERO:
                    continue
                pnl = resp.price * resp.filled - filled_leg.cost_basis
            else:
                continue

            # Cancel BOTH sides' resting orders to prevent overfill
            await self._cancel_orders_for_market(opp)

            result = SpreadResult(
                opportunity=opp,
                state=PositionState.SETTLED,
                up_entry=pos.up_leg.entry_price,
                up_qty=pos.up_leg.quantity,
                down_entry=pos.down_leg.entry_price if pos.down_leg else None,
                down_qty=pos.down_leg.quantity if pos.down_leg else None,
                total_cost_basis=filled_leg.cost_basis,
                entry_time=pos.entry_time,
                exit_time=int(time.time()),
                winning_side=filled_side,
                pnl=pnl,
                is_paper=pos.is_paper,
                order_ids=tuple(filled_leg.order_ids),
                outcome_known=True,
            )
            self._results.append(result)
            await self._persist_result(result)
            self._settled_cids.add(cid)
            del self._positions[cid]

            mode = "PAPER" if pos.is_paper else "LIVE"
            logger.info(
                "%s TAKE-PROFIT %s sell %s at bid=%.4f entry=%.4f profit=%.4f qty=%.0f asset=%s",
                mode,
                cid[:12],
                filled_side,
                best_bid,
                filled_leg.entry_price,
                pnl,
                filled_leg.quantity,
                opp.asset,
            )

    def _get_best_bid(self, token_id: str) -> Decimal | None:
        """Return the best bid price for a token from the WS feed cache.

        Args:
            token_id: CLOB token identifier.

        Returns:
            Best bid price or ``None`` if unavailable.

        """
        if self._book_feed is None:
            return None
        book = self._book_feed.get_book(token_id)
        if book is None or not book.bids:
            return None
        return max(level.price for level in book.bids)

    async def _record_expired_loss(
        self, cid: str, pos: PairedPosition, *, up_still_open: bool
    ) -> None:
        """Transition an expired pending position with one side filled to SINGLE_LEG.

        Instead of recording an immediate total loss, keep the position so
        ``_settle_expired_positions()`` can resolve the outcome via Binance
        and compute the actual P&L (win $0.70 or lose $0.30 on a $0.30 bet).

        Args:
            cid: Condition ID.
            pos: The expired position.
            up_still_open: Whether the Up order was still open (unfilled).
            now: Current epoch seconds.

        """
        down_still_open = not up_still_open or (
            pos.pending_down_order_id is not None and up_still_open
        )
        one_filled = up_still_open != down_still_open
        if not one_filled:
            logger.info("LIVE CANCELLED %s: market expired, no fills", cid[:12])
            return

        filled_side = "Down" if up_still_open else "Up"
        # Transition to SINGLE_LEG so normal settlement handles the outcome
        pos.state = PositionState.SINGLE_LEG
        if up_still_open:
            pos.down_leg = pos.down_leg  # keep filled Down leg
        else:
            pos.down_leg = None  # drop unfilled Down leg
        pos.pending_up_order_id = None
        pos.pending_down_order_id = None
        logger.info(
            "LIVE SINGLE-LEG %s %s filled at expiry, awaiting settlement asset=%s",
            cid[:12],
            filled_side,
            pos.opportunity.asset,
        )

    async def _handle_expired_pending(
        self,
        cid: str,
        pos: PairedPosition,
        *,
        up_still_open: bool,
        down_still_open: bool,
    ) -> None:
        """Handle a pending position whose market window has expired.

        Cancel open orders, then either transition single-filled positions
        to SINGLE_LEG for proper settlement, or remove unfilled positions.

        Args:
            cid: Condition ID.
            pos: The expired pending position.
            up_still_open: Whether the Up order is still open.
            down_still_open: Whether the Down order is still open.

        """
        await self._cancel_pending_orders(pos, up_open=up_still_open, down_open=down_still_open)
        await self._record_expired_loss(cid, pos, up_still_open=up_still_open)
        if pos.state != PositionState.SINGLE_LEG:
            self._settled_cids.add(cid)
            del self._positions[cid]

    async def _manage_pending_orders(self) -> None:
        """Check fill status of pending GTC limit orders and transition states.

        For each PENDING position:
        - If both orders are no longer open (filled) -> transition to PAIRED.
        - If ``single_leg_timeout`` elapsed with one side unfilled -> cancel
          unfilled order, unwind filled side, and mark SINGLE_LEG.
        - If neither filled and market is about to expire -> cancel both
          and remove position.

        For paper-mode maker positions, delegate to
        ``_manage_paper_maker_orders()`` which simulates fills using
        order book snapshots.
        """
        # Handle paper-mode maker fills via order book simulation
        if self.config.strategy == "maker":
            await self._manage_paper_maker_orders()
            await self._maybe_hedge_maker_positions()
            await self._maybe_take_profit_maker()

        if self.client is None or not self.live:
            return

        pending = [
            (cid, pos)
            for cid, pos in self._positions.items()
            if pos.state == PositionState.PENDING and not pos.is_paper
        ]
        if not pending:
            return

        try:
            open_orders = await self.client.get_open_orders()
        except (httpx.HTTPError, Exception):
            logger.debug("Failed to fetch open orders for pending management")
            return

        open_order_ids = {o.order_id for o in open_orders}
        now = int(time.time())

        for cid, pos in pending:
            up_still_open = (
                pos.pending_up_order_id is not None and pos.pending_up_order_id in open_order_ids
            )
            down_still_open = (
                pos.pending_down_order_id is not None
                and pos.pending_down_order_id in open_order_ids
            )

            # Market about to expire — cancel and handle
            if pos.opportunity.window_end_ts <= now:
                await self._handle_expired_pending(
                    cid, pos, up_still_open=up_still_open, down_still_open=down_still_open
                )
                continue

            # Both filled — transition to PAIRED
            if not up_still_open and not down_still_open:
                pos.state = PositionState.PAIRED
                pos.pending_up_order_id = None
                pos.pending_down_order_id = None
                logger.info(
                    "LIVE PAIRED %s: both GTC orders filled, margin=%.4f asset=%s",
                    cid[:12],
                    pos.opportunity.margin,
                    pos.opportunity.asset,
                )
                continue

            # Maker strategy: mark filled sides so hedge can see them,
            # then let the hedge run before the timeout kicks in.
            if self.config.strategy == "maker":
                if not up_still_open and pos.pending_up_order_id is not None:
                    pos.pending_up_order_id = None
                if not down_still_open and pos.pending_down_order_id is not None:
                    pos.pending_down_order_id = None
                # Skip timeout — let hedge and window expiry handle maker positions
                continue

            # Timeout — one side filled, cancel the other and try to unwind
            elapsed = now - pos.entry_time
            if elapsed >= self.config.single_leg_timeout:
                removed = await self._handle_pending_timeout(
                    cid, pos, up_still_open=up_still_open, down_still_open=down_still_open
                )
                if removed:
                    continue

    async def _handle_pending_timeout(
        self,
        cid: str,
        pos: PairedPosition,
        *,
        up_still_open: bool,
        down_still_open: bool,
    ) -> bool:
        """Handle timeout for a pending position with partial fills.

        Cancel unfilled orders and attempt to unwind filled legs.
        Transition to SINGLE_LEG if unwind fails.

        Args:
            cid: Condition ID of the position.
            pos: The pending position.
            up_still_open: Whether the Up order is still open.
            down_still_open: Whether the Down order is still open.

        Returns:
            ``True`` if the position was removed from ``_positions``.

        """
        if up_still_open and not down_still_open:
            await self._cancel_order_safe(pos.pending_up_order_id)
            if pos.down_leg is not None:
                unwind_pnl = await self._unwind_filled_leg(
                    pos.opportunity.down_token_id, pos.down_leg
                )
                if unwind_pnl != ZERO:
                    self._settled_cids.add(cid)
                    del self._positions[cid]
                    logger.info(
                        "LIVE UNWIND %s: Up timed out, sold Down pnl=%.4f", cid[:12], unwind_pnl
                    )
                    return True
            pos.state = PositionState.SINGLE_LEG
            logger.warning("LIVE SINGLE-LEG %s: Up order timed out, Down filled", cid[:12])
        elif down_still_open and not up_still_open:
            await self._cancel_order_safe(pos.pending_down_order_id)
            unwind_pnl = await self._unwind_filled_leg(pos.opportunity.up_token_id, pos.up_leg)
            if unwind_pnl != ZERO:
                self._settled_cids.add(cid)
                del self._positions[cid]
                logger.info(
                    "LIVE UNWIND %s: Down timed out, sold Up pnl=%.4f", cid[:12], unwind_pnl
                )
                return True
            pos.state = PositionState.SINGLE_LEG
            pos.down_leg = None
            logger.warning("LIVE SINGLE-LEG %s: Down order timed out, Up filled", cid[:12])
        else:
            await self._cancel_pending_orders(pos, up_open=up_still_open, down_open=down_still_open)
            self._settled_cids.add(cid)
            del self._positions[cid]
            logger.warning("LIVE CANCELLED %s: both orders timed out", cid[:12])
            return True

        pos.pending_up_order_id = None
        pos.pending_down_order_id = None
        return False

    async def _cancel_pending_orders(
        self,
        pos: PairedPosition,
        *,
        up_open: bool,
        down_open: bool,
    ) -> None:
        """Cancel any still-open pending orders on a position.

        Args:
            pos: The position whose orders to cancel.
            up_open: Whether the Up order is still open.
            down_open: Whether the Down order is still open.

        """
        if up_open and pos.pending_up_order_id is not None:
            await self._cancel_order_safe(pos.pending_up_order_id)
        if down_open and pos.pending_down_order_id is not None:
            await self._cancel_order_safe(pos.pending_down_order_id)

    async def _cancel_orders_for_market(self, opp: SpreadOpportunity) -> None:
        """Cancel any open orders on a market's token IDs.

        Called after a failed order placement to clean up orders that may
        have been accepted by the CLOB despite an error response.

        Args:
            opp: The opportunity whose token orders to cancel.

        """
        if self.client is None:
            return
        try:
            open_orders = await self.client.get_open_orders()
        except (httpx.HTTPError, Exception):
            return
        token_ids = {opp.up_token_id, opp.down_token_id}
        for order in open_orders:
            if order.token_id in token_ids:
                await self._cancel_order_safe(order.order_id)
                logger.info(
                    "Cancelled orphaned order %s for %s",
                    order.order_id[:12],
                    opp.condition_id[:12],
                )

    async def _cancel_order_safe(self, order_id: str | None) -> None:
        """Cancel an order, swallowing errors.

        Args:
            order_id: CLOB order ID to cancel, or ``None`` to skip.

        """
        if order_id is None or self.client is None:
            return
        try:
            await self.client.cancel_order(order_id)
        except (httpx.HTTPError, Exception):
            logger.debug("Failed to cancel order %s", order_id)

    async def _settle_expired_positions(self) -> None:
        """Settle positions whose market windows have expired.

        For PAIRED positions, guaranteed profit: winning side pays $1.00/token.
        For SINGLE_LEG positions, resolve via Binance candles.
        In live mode, check Polymarket redeemable positions first.
        """
        now = int(time.time())
        expired = [
            cid
            for cid, pos in self._positions.items()
            if pos.opportunity.window_end_ts <= now and pos.state != PositionState.PENDING
        ]

        for cid in expired:
            self._settled_cids.add(cid)
            pos = self._positions.pop(cid)

            # Use live resolution when in live mode
            if self.live:
                winning_side = await self._resolve_outcome_live(pos)
            else:
                winning_side = await self._resolve_outcome(pos)

            outcome_known = winning_side is not None

            pnl = self._compute_pnl(pos, winning_side)

            result = SpreadResult(
                opportunity=pos.opportunity,
                state=PositionState.SETTLED,
                up_entry=pos.up_leg.entry_price,
                up_qty=pos.up_leg.quantity,
                down_entry=pos.down_leg.entry_price if pos.down_leg else None,
                down_qty=pos.down_leg.quantity if pos.down_leg else None,
                total_cost_basis=pos.total_cost_basis,
                entry_time=pos.entry_time,
                exit_time=now,
                winning_side=winning_side,
                pnl=pnl,
                is_paper=pos.is_paper,
                order_ids=tuple(pos.all_order_ids),
                outcome_known=outcome_known,
            )
            self._results.append(result)
            await self._persist_result(result)

            # Update high-water mark
            if pnl > ZERO:
                current_total = self._total_capital()
                self._high_water_mark = max(self._high_water_mark, current_total)
                self._consecutive_losses = 0
            elif pnl < ZERO:
                self._record_loss()

            outcome_label = "WIN" if pnl > ZERO else "LOSS"
            if not outcome_known:
                outcome_label = "UNKNOWN"
            mode = "PAPER" if pos.is_paper else "LIVE"
            logger.info(
                "%s CLOSE %s %s pnl=%.4f cost=$%.4f winning=%s state=%s asset=%s",
                mode,
                cid[:12],
                outcome_label,
                pnl,
                pos.total_cost_basis,
                winning_side,
                pos.state.value,
                pos.opportunity.asset,
            )

    def _compute_pnl(self, pos: PairedPosition, winning_side: str | None) -> Decimal:
        """Compute P&L for a position at settlement, deducting entry fees.

        For PAIRED positions, the winning leg pays $1.00/token:
        ``winning_qty * 1.0 - total_cost - total_fees``.

        For SINGLE_LEG positions:
        - If the single leg wins: ``qty * 1.0 - cost_basis - fees``
        - If the single leg loses: ``0 - cost_basis``
        - If outcome unknown: ``0 - cost_basis`` (conservative)

        Args:
            pos: The position being settled.
            winning_side: ``"Up"`` or ``"Down"``, or ``None``.

        Returns:
            Realised P&L in USDC (net of fees).

        """
        # Compute entry fees for both legs
        up_fee = (
            compute_poly_fee(pos.up_leg.entry_price, self.config.fee_rate, self.config.fee_exponent)
            * pos.up_leg.quantity
        )
        down_fee = ZERO
        if pos.down_leg is not None:
            down_fee = (
                compute_poly_fee(
                    pos.down_leg.entry_price, self.config.fee_rate, self.config.fee_exponent
                )
                * pos.down_leg.quantity
            )
        total_fees = up_fee + down_fee

        if pos.state == PositionState.PAIRED and pos.down_leg is not None:
            if winning_side is None:
                # Hedged but unknown outcome — can't determine which qty wins.
                # Use the smaller qty as conservative estimate.
                winning_qty = min(pos.up_leg.quantity, pos.down_leg.quantity)
            elif winning_side == "Up":
                winning_qty = pos.up_leg.quantity
            else:
                winning_qty = pos.down_leg.quantity
            return winning_qty * _WIN_PRICE - pos.total_cost_basis - total_fees

        # SINGLE_LEG: only Up leg exists
        if winning_side == "Up":
            return pos.up_leg.quantity * _WIN_PRICE - pos.up_leg.cost_basis - total_fees
        if winning_side == "Down":
            return ZERO - pos.up_leg.cost_basis
        # Unknown outcome, conservative loss
        return ZERO - pos.up_leg.cost_basis

    async def _resolve_outcome_live(self, pos: PairedPosition) -> str | None:
        """Resolve outcome using Polymarket's actual resolution status first.

        Check if the market has any redeemable positions (indicating
        resolution), and fall back to Binance candle-based resolution
        if no match is found.

        Args:
            pos: The position with opportunity metadata.

        Returns:
            ``"Up"`` or ``"Down"`` if resolved, ``None`` otherwise.

        """
        if self.client is None:
            return await self._resolve_outcome(pos)
        try:
            positions = await self.client.get_redeemable_positions()
            for rp in positions:
                if rp.condition_id == pos.opportunity.condition_id:
                    return rp.outcome
        except (httpx.HTTPError, Exception):
            logger.debug("Failed to check redeemable positions, falling back to Binance")
        return await self._resolve_outcome(pos)

    async def _resolve_outcome(self, pos: PairedPosition) -> str | None:
        """Determine which side won via Binance spot price movement.

        Fetch Binance 1-min candles for the market window and compare
        opening vs closing price.  Return ``None`` when candle data is
        unavailable.

        Args:
            pos: The position with opportunity timestamps.

        Returns:
            ``"Up"`` if price went up, ``"Down"`` if down, ``None`` if
            candle data was unavailable.

        """
        if self._binance is None:
            logger.warning("Binance client not initialised, cannot resolve outcome")
            return None

        opp = pos.opportunity
        try:
            provider = BinanceCandleProvider(self._binance)
            candles = await provider.get_candles(
                opp.asset,
                Interval.M1,
                opp.window_start_ts,
                opp.window_end_ts,
            )
        except (BinanceError, httpx.HTTPError, KeyError, ValueError):
            logger.warning("Failed to fetch candles for %s, outcome unknown", opp.asset)
            return None

        if not candles:
            logger.warning("No candles for %s window, outcome unknown", opp.asset)
            return None

        open_price = candles[0].open
        close_price = candles[-1].close
        if close_price > open_price:
            winning_side = "Up"
        elif close_price < open_price:
            winning_side = "Down"
        else:
            # Flat market — no clear winner; conservative path handles None
            winning_side = None

        logger.info(
            "  SPOT %s open=%.2f close=%.2f direction=%s",
            opp.asset,
            open_price,
            close_price,
            winning_side,
        )
        return winning_side

    def _check_drawdown_halt(self) -> bool:
        """Return ``True`` when session drawdown exceeds the configured limit.

        Returns:
            ``True`` if new entries should be blocked.

        """
        if self._session_start_capital <= ZERO:
            return False
        total_pnl = sum((r.pnl for r in self._results), start=ZERO)
        max_loss = self.config.max_drawdown_pct * self._session_start_capital
        return total_pnl < ZERO - max_loss

    def _record_loss(self) -> None:
        """Increment consecutive losses and activate circuit breaker if needed."""
        self._consecutive_losses += 1
        threshold = self.config.circuit_breaker_losses
        if threshold > 0 and self._consecutive_losses >= threshold:
            self._circuit_breaker_until = int(time.time()) + self.config.circuit_breaker_cooldown
            logger.warning(
                "CIRCUIT-BREAKER triggered after %d consecutive losses, pausing for %ds",
                self._consecutive_losses,
                self.config.circuit_breaker_cooldown,
            )

    def _log_heartbeat(self) -> None:
        """Emit a heartbeat via the shared HeartbeatLogger."""
        total_pnl = sum((r.pnl for r in self._results), start=ZERO)
        scanner_markets = self._scanner.known_market_count if self._scanner else 0
        self._heartbeat.maybe_log(
            polls=self._poll_count,
            known_markets=scanner_markets,
            paired=sum(1 for p in self._positions.values() if p.state == PositionState.PAIRED),
            single_leg=sum(
                1 for p in self._positions.values() if p.state == PositionState.SINGLE_LEG
            ),
            closed=len(self._results),
            pnl=float(total_pnl),
        )

    def _log_periodic_summary(self) -> None:
        """Log a detailed session summary every 15 minutes."""
        total_pnl = sum((r.pnl for r in self._results), start=ZERO)
        wins = sum(1 for r in self._results if r.pnl > ZERO)
        losses = sum(1 for r in self._results if r.pnl < ZERO)
        win_rate = (wins / len(self._results) * 100) if self._results else 0.0
        paired = [p for p in self._positions.values() if p.state == PositionState.PAIRED]
        single = [p for p in self._positions.values() if p.state == PositionState.SINGLE_LEG]
        capital = self._get_capital()

        logger.info("=" * 60)
        logger.info(
            "SUMMARY | capital=$%.2f | pnl=$%.2f | hwm=$%.2f | wins=%d losses=%d (%.0f%%)",
            capital,
            total_pnl,
            self._high_water_mark,
            wins,
            losses,
            win_rate,
        )
        logger.info(
            "SUMMARY | open=%d (paired=%d single=%d) | closed=%d | polls=%d",
            len(self._positions),
            len(paired),
            len(single),
            len(self._results),
            self._poll_count,
        )
        for cid, pos in self._positions.items():
            combined = pos.up_leg.entry_price
            if pos.down_leg is not None:
                combined += pos.down_leg.entry_price
            logger.info(
                "SUMMARY |   %s %s %s combined=%.4f cost=$%.2f",
                pos.state.value.upper(),
                cid[:12],
                pos.opportunity.asset,
                combined,
                pos.total_cost_basis,
            )
        logger.info("=" * 60)

    def _log_summary(self) -> None:
        """Log a final session summary on shutdown."""
        total_pnl = sum((r.pnl for r in self._results), start=ZERO)
        logger.info(
            "SESSION SUMMARY polls=%d closed=%d open=%d pnl=%.4f",
            self._poll_count,
            len(self._results),
            len(self._positions),
            total_pnl,
        )

    async def _persist_result(self, result: SpreadResult) -> None:
        """Persist a settled trade result to the database if a repo is attached.

        Args:
            result: The settled trade result to persist.

        """
        if self._repo is None:
            return
        try:
            record = SpreadResultRecord.from_spread_result(result)
            await self._repo.save_result(record)
        except (OSError, ValueError, KeyError):
            logger.exception(
                "Failed to persist spread result for %s",
                result.opportunity.condition_id[:12],
            )

    @property
    def positions(self) -> dict[str, PairedPosition]:
        """Return the current open positions (read-only copy)."""
        return dict(self._positions)

    @property
    def results(self) -> list[SpreadResult]:
        """Return all closed trade results (read-only copy)."""
        return list(self._results)

    @property
    def poll_count(self) -> int:
        """Return the number of completed poll cycles."""
        return self._poll_count
