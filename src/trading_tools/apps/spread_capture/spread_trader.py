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
from trading_tools.core.models import ONE, ZERO, Interval, Side
from trading_tools.data.providers.binance import BinanceCandleProvider

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

    def __post_init__(self) -> None:
        """Initialize shared services when running in live mode."""
        if self.live and self.client is not None:
            self._redeemer = PositionRedeemer(client=self.client)
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

        self._scanner = MarketScanner(
            client=self.client,
            series_slugs=self.config.series_slugs,
            max_combined_cost=self.config.max_combined_cost,
            min_spread_margin=self.config.min_spread_margin,
            max_window_seconds=self.config.max_window_seconds,
            max_entry_age_pct=self.config.max_entry_age_pct,
            rediscovery_interval=self.config.rediscovery_interval,
            fee_rate=self.config.fee_rate,
            fee_exponent=self.config.fee_exponent,
        )
        self._binance = BinanceClient()
        self._shutdown.install()

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
            self._log_summary()
            if self._binance is not None:  # pyright: ignore[reportUnnecessaryComparison]
                await self._binance.close()

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

        opportunities = await self._scanner.scan(set(self._positions.keys()))

        for opp in opportunities:
            if len(self._positions) >= self.config.max_open_positions:
                break
            await self._enter_spread(opp)

        await self._settle_expired_positions()

        if self._redeemer is not None:
            await self._redeemer.redeem_if_available()

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
                del self._positions[cid]
                logger.info(
                    "LIVE UNWIND %s single-leg early exit pnl=%.4f",
                    cid[:12],
                    pnl,
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

    async def _manage_pending_orders(self) -> None:
        """Check fill status of pending GTC limit orders and transition states.

        For each PENDING position:
        - If both orders are no longer open (filled) -> transition to PAIRED.
        - If ``single_leg_timeout`` elapsed with one side unfilled -> cancel
          unfilled order, unwind filled side, and mark SINGLE_LEG.
        - If neither filled and market is about to expire -> cancel both
          and remove position.
        """
        if self.client is None:
            return

        pending = [
            (cid, pos) for cid, pos in self._positions.items() if pos.state == PositionState.PENDING
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

            # Market about to expire — cancel everything
            if pos.opportunity.window_end_ts <= now:
                await self._cancel_pending_orders(
                    pos, up_open=up_still_open, down_open=down_still_open
                )
                del self._positions[cid]
                logger.info(
                    "LIVE CANCELLED %s: market expired while pending",
                    cid[:12],
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
