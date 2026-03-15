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
from trading_tools.clients.binance.client import BinanceClient
from trading_tools.clients.binance.exceptions import BinanceError
from trading_tools.core.models import ZERO, Interval
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
_ONE = Decimal(1)
_SUMMARY_INTERVAL = 900  # 15 minutes


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
        new spread positions, and settle expired ones.
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

        opportunities = await self._scanner.scan(set(self._positions.keys()))

        for opp in opportunities:
            if len(self._positions) >= self.config.max_open_positions:
                break
            await self._enter_spread(opp)

        await self._settle_expired_positions()

        if self._redeemer is not None:
            await self._redeemer.redeem_if_available()

    async def _enter_spread(self, opp: SpreadOpportunity) -> None:
        """Enter a spread position by buying both sides simultaneously.

        Skip if capital is insufficient, drawdown halt is active, or
        circuit breaker is cooling down.

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

        # Apply paper slippage: worsen both prices
        up_price = opp.up_price
        down_price = opp.down_price
        if not self.live and self.config.paper_slippage_pct > ZERO:
            up_price = up_price * (_ONE + self.config.paper_slippage_pct)
            down_price = down_price * (_ONE + self.config.paper_slippage_pct)

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
            result = await self._place_spread_orders(opp, up_leg, down_leg)
            up_leg_result, down_leg_result = result
            if up_leg_result is None and down_leg_result is None:
                logger.warning("Both leg orders failed for %s", opp.condition_id[:12])
                return

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
                    pending_down_order_id=(
                        down_leg_result.order_ids[-1] if down_leg_result else None
                    ),
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
                return

            # FOK market order path: immediate fill or fail
            if up_leg_result is None:
                logger.warning("Up leg order failed for %s", opp.condition_id[:12])
                return
            if down_leg_result is None:
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
                return
            up_leg = up_leg_result
            down_leg = down_leg_result

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
            _ONE - combined_actual,
            qty,
            up_cost + down_cost,
            opp.asset,
        )

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
          unfilled order and mark SINGLE_LEG.
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

            # Timeout — one side filled, cancel the other
            elapsed = now - pos.entry_time
            if elapsed >= self.config.single_leg_timeout:
                if up_still_open and not down_still_open:
                    # Down filled, Up unfilled — cancel Up
                    await self._cancel_order_safe(pos.pending_up_order_id)
                    pos.state = PositionState.SINGLE_LEG
                    # The filled side is Down; swap so up_leg holds the filled leg
                    # Actually keep as-is: SINGLE_LEG with down_leg filled
                    logger.warning(
                        "LIVE SINGLE-LEG %s: Up order timed out, Down filled",
                        cid[:12],
                    )
                elif down_still_open and not up_still_open:
                    # Up filled, Down unfilled — cancel Down
                    await self._cancel_order_safe(pos.pending_down_order_id)
                    pos.state = PositionState.SINGLE_LEG
                    pos.down_leg = None
                    logger.warning(
                        "LIVE SINGLE-LEG %s: Down order timed out, Up filled",
                        cid[:12],
                    )
                else:
                    # Both still open after timeout — cancel both, remove
                    await self._cancel_pending_orders(
                        pos, up_open=up_still_open, down_open=down_still_open
                    )
                    del self._positions[cid]
                    logger.warning(
                        "LIVE CANCELLED %s: both orders timed out",
                        cid[:12],
                    )

                pos.pending_up_order_id = None
                pos.pending_down_order_id = None

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
        """
        now = int(time.time())
        expired = [
            cid
            for cid, pos in self._positions.items()
            if pos.opportunity.window_end_ts <= now and pos.state != PositionState.PENDING
        ]

        for cid in expired:
            pos = self._positions.pop(cid)
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
        """Compute P&L for a position at settlement.

        For PAIRED positions, the winning leg pays $1.00/token:
        ``winning_qty * 1.0 - total_cost``.

        For SINGLE_LEG positions:
        - If the single leg wins: ``qty * 1.0 - cost_basis``
        - If the single leg loses: ``0 - cost_basis``
        - If outcome unknown: ``0 - cost_basis`` (conservative)

        Args:
            pos: The position being settled.
            winning_side: ``"Up"`` or ``"Down"``, or ``None``.

        Returns:
            Realised P&L in USDC.

        """
        if pos.state == PositionState.PAIRED and pos.down_leg is not None:
            if winning_side is None:
                # Hedged but unknown outcome — can't determine which qty wins.
                # Use the smaller qty as conservative estimate.
                winning_qty = min(pos.up_leg.quantity, pos.down_leg.quantity)
            elif winning_side == "Up":
                winning_qty = pos.up_leg.quantity
            else:
                winning_qty = pos.down_leg.quantity
            return winning_qty * _WIN_PRICE - pos.total_cost_basis

        # SINGLE_LEG: only Up leg exists
        if winning_side == "Up":
            return pos.up_leg.quantity * _WIN_PRICE - pos.up_leg.cost_basis
        if winning_side == "Down":
            return ZERO - pos.up_leg.cost_basis
        # Unknown outcome, conservative loss
        return ZERO - pos.up_leg.cost_basis

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
        total_pnl = sum(r.pnl for r in self._results)
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
        total_pnl = sum(r.pnl for r in self._results)
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
        total_pnl = sum(r.pnl for r in self._results)
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
