"""Accumulating spread capture trading engine.

Implement a whale-inspired strategy that buys each side of an Up/Down
market independently whenever the ask price dips below a configurable
threshold.  Over many small fills across the market window, the combined
VWAP of both legs is driven well below $1.00, locking in profit at
settlement.

Unlike the simultaneous ``SpreadTrader`` that requires both sides to be
cheap at the same instant, ``AccumulatingTrader`` accumulates positions
opportunistically — one side at a time — mimicking the behaviour observed
in high-profit whale wallets.
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
from trading_tools.apps.spread_capture.fees import compute_poly_fee
from trading_tools.clients.binance.client import BinanceClient
from trading_tools.clients.binance.exceptions import BinanceError
from trading_tools.core.models import ONE, ZERO, Interval
from trading_tools.data.providers.binance import BinanceCandleProvider

from .market_scanner import MarketScanner
from .models import (
    AccumulatingPosition,
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


def _empty_accum_dict() -> dict[str, AccumulatingPosition]:
    """Return an empty dict for dataclass default_factory."""
    return {}


def _empty_result_list() -> list[SpreadResult]:
    """Return an empty list for dataclass default_factory."""
    return []


@dataclass
class AccumulatingTrader:
    """Accumulating spread capture engine for Polymarket Up/Down markets.

    Buy each side of a spread independently over time, filling small
    orders whenever the ask price dips below the per-side threshold.
    Track combined VWAP and imbalance ratio to ensure the position
    remains profitable and balanced.

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
    _positions: dict[str, AccumulatingPosition] = field(
        default_factory=_empty_accum_dict, repr=False
    )
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
        scans for per-side fill opportunities, accumulates positions via
        small fills, and settles expired positions at market close.
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
            "accumulate started mode=%s poll=%ds capital=$%s"
            " threshold=%.4f max_vwap=%.4f max_imbal=%.1f"
            " fill_size=%s max_open=%d slugs=%s",
            mode,
            self.config.poll_interval,
            capital,
            self.config.per_side_ask_threshold,
            self.config.max_combined_vwap,
            self.config.max_imbalance_ratio,
            self.config.fill_size_tokens,
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

    # ------------------------------------------------------------------
    # Capital management
    # ------------------------------------------------------------------

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

    def _budget_for_market(self) -> Decimal:
        """Return the per-market budget based on position sizing config.

        Returns:
            Budget in USDC for a single market position.

        """
        return self._total_capital() * self.config.max_position_pct

    # ------------------------------------------------------------------
    # Poll cycle
    # ------------------------------------------------------------------

    async def _poll_cycle(self) -> None:
        """Execute one scan-fill-settle cycle.

        Settle expired positions, scan for per-side opportunities, open
        new accumulating positions, and attempt fills on each open position.
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

        await self._settle_expired_positions()

        # Scan for new markets to track
        opportunities = await self._scanner.scan_per_side(
            set(self._positions.keys()),
            self.config.per_side_ask_threshold,
        )

        # Open new accumulating positions for discovered markets
        for opp in opportunities:
            if len(self._positions) >= self.config.max_open_positions:
                break
            if self._check_drawdown_halt():
                break
            now = int(time.time())
            if self._circuit_breaker_until > now:
                break
            self._open_accumulating_position(opp)

        # Attempt fills on all open accumulating positions
        await self._attempt_fills()

        if self._redeemer is not None:
            await self._redeemer.redeem_if_available()

    def _open_accumulating_position(self, opp: SpreadOpportunity) -> None:
        """Create a new accumulating position with zero-quantity legs.

        Args:
            opp: The spread opportunity to start tracking.

        """
        now = int(time.time())
        budget = self._budget_for_market()

        pos = AccumulatingPosition(
            opportunity=opp,
            state=PositionState.ACCUMULATING,
            up_leg=SideLeg(side="Up", entry_price=ZERO, quantity=ZERO, cost_basis=ZERO),
            down_leg=SideLeg(side="Down", entry_price=ZERO, quantity=ZERO, cost_basis=ZERO),
            entry_time=now,
            is_paper=not self.live,
            budget=budget,
        )
        self._positions[opp.condition_id] = pos

        mode = "LIVE" if self.live else "PAPER"
        logger.info(
            "%s TRACK %s budget=$%.2f threshold=%.4f asset=%s",
            mode,
            opp.condition_id[:12],
            budget,
            self.config.per_side_ask_threshold,
            opp.asset,
        )

    # ------------------------------------------------------------------
    # Fill logic
    # ------------------------------------------------------------------

    async def _attempt_fills(self) -> None:
        """Attempt per-side fills on all open accumulating positions.

        For each position, re-fetch order books and independently decide
        whether to fill the Up and/or Down side based on current ask prices.
        """
        if self.client is None:
            return

        for cid in list(self._positions.keys()):
            pos = self._positions.get(cid)
            if pos is None or pos.state != PositionState.ACCUMULATING:
                continue

            try:
                up_book, down_book = await asyncio.gather(
                    self.client.get_order_book(pos.opportunity.up_token_id),
                    self.client.get_order_book(pos.opportunity.down_token_id),
                )
            except (httpx.HTTPError, Exception):
                logger.debug("Failed to fetch books for %s", cid[:12])
                continue

            up_ask = up_book.asks[0].price if up_book.asks else None
            down_ask = down_book.asks[0].price if down_book.asks else None
            up_depth = sum((level.size for level in up_book.asks), start=ZERO)
            down_depth = sum((level.size for level in down_book.asks), start=ZERO)

            # Try filling each side independently
            if up_ask is not None:
                await self._try_fill_side(pos, "Up", up_ask, up_depth)
            if down_ask is not None:
                await self._try_fill_side(pos, "Down", down_ask, down_depth)

    async def _try_fill_side(
        self,
        pos: AccumulatingPosition,
        side: str,
        ask_price: Decimal,
        depth: Decimal,
    ) -> None:
        """Attempt a single fill on one side of an accumulating position.

        Check the ask threshold, hypothetical VWAP, imbalance ratio, and
        budget before executing a fill.

        Args:
            pos: The accumulating position.
            side: ``"Up"`` or ``"Down"``.
            ask_price: Current best ask price for this side.
            depth: Total visible ask depth for this side.

        """
        fill_qty = self._compute_fill_qty(pos, side, ask_price, depth)
        if fill_qty is None:
            return

        if not self._should_fill_side(pos, side, ask_price, fill_qty):
            return

        # Execute the fill
        fill_price = ask_price
        if not self.live:
            fill_price = ask_price * (ONE + self.config.paper_slippage_pct)
        elif self._executor is not None:
            token_id = (
                pos.opportunity.up_token_id if side == "Up" else pos.opportunity.down_token_id
            )
            try:
                resp = await self._executor.place_order(token_id, "BUY", ask_price, fill_qty)
                if resp is None or resp.filled <= ZERO:
                    return
                fill_price = resp.price
                fill_qty = resp.filled
                leg = pos.up_leg if side == "Up" else pos.down_leg
                leg.order_ids.append(resp.order_id)
            except (httpx.HTTPError, Exception):
                logger.debug("Live fill failed for %s %s", pos.opportunity.condition_id[:12], side)
                return

        leg = pos.up_leg if side == "Up" else pos.down_leg
        if leg.quantity == ZERO:
            # First fill — set initial values
            leg.entry_price = fill_price
            leg.quantity = fill_qty
            leg.cost_basis = fill_price * fill_qty
        else:
            leg.add_fill(fill_price, fill_qty)

        mode = "LIVE" if self.live else "PAPER"
        logger.info(
            "%s FILL %s %s price=%.4f qty=%.1f vwap=%.4f"
            " combined_vwap=%.4f total_up=%.1f total_down=%.1f",
            mode,
            pos.opportunity.condition_id[:12],
            side,
            fill_price,
            fill_qty,
            leg.entry_price,
            pos.combined_vwap,
            pos.up_leg.quantity,
            pos.down_leg.quantity,
        )

    def _compute_fill_qty(
        self,
        pos: AccumulatingPosition,
        side: str,  # noqa: ARG002
        ask_price: Decimal,
        depth: Decimal,
    ) -> Decimal | None:
        """Compute the fill quantity, clamped by depth, budget, and minimum.

        Args:
            pos: The accumulating position.
            side: ``"Up"`` or ``"Down"`` (reserved for future per-side budgets).
            ask_price: Current best ask price.
            depth: Total visible ask depth.

        Returns:
            Fill quantity in tokens, or ``None`` if below minimum.

        """
        # Start with configured fill size
        qty = self.config.fill_size_tokens

        # Cap by order book depth
        max_from_depth = depth * self.config.max_book_pct
        qty = min(qty, max_from_depth)

        # Cap by remaining budget
        budget_remaining = pos.budget - pos.total_cost_basis
        if budget_remaining <= ZERO:
            return None
        max_from_budget = (budget_remaining / ask_price).quantize(Decimal("0.01"))
        qty = min(qty, max_from_budget)

        # Quantize and check minimum
        qty = qty.quantize(Decimal("0.01"))
        if qty < _MIN_TOKEN_QTY:
            return None

        return qty

    def _should_fill_side(
        self,
        pos: AccumulatingPosition,
        side: str,
        ask_price: Decimal,
        fill_qty: Decimal,
    ) -> bool:
        """Decide whether to execute a fill on one side of a position.

        Check the ask price threshold, hypothetical combined VWAP after
        fill, and imbalance ratio constraints.

        Args:
            pos: The accumulating position.
            side: ``"Up"`` or ``"Down"``.
            ask_price: Current best ask price.
            fill_qty: Proposed fill quantity.

        Returns:
            ``True`` if the fill should proceed.

        """
        if ask_price >= self.config.per_side_ask_threshold:
            return False

        # Cap single-side spend when other side has no fills yet
        if self._would_exceed_single_side_cap(pos, side, ask_price, fill_qty):
            return False

        # Check hypothetical combined VWAP.
        # When both sides already have fills and the VWAP is above target,
        # only allow fills that *improve* (lower) the combined VWAP.  The
        # whale edge comes from time-averaging dips over many fills — we
        # must let improving fills through even when VWAP is above target.
        current_vwap = pos.combined_vwap
        hyp_vwap = self._hypothetical_combined_vwap(pos, side, ask_price, fill_qty)
        if hyp_vwap > ZERO and hyp_vwap > self.config.max_combined_vwap:
            if current_vwap <= ZERO:
                # First time both sides have fills — allow to bootstrap
                pass
            elif hyp_vwap < current_vwap:
                # Fill improves (lowers) the combined VWAP — allow
                pass
            else:
                # Fill would worsen or maintain above-target VWAP — block
                return False

        # Check imbalance ratio
        return not self._would_exceed_imbalance(pos, side, fill_qty)

    def _would_exceed_single_side_cap(
        self,
        pos: AccumulatingPosition,
        side: str,
        ask_price: Decimal,
        fill_qty: Decimal,
    ) -> bool:
        """Check if a fill would exceed the single-side budget cap.

        When one side has fills but the other does not, limit total spend
        on the filled side to ``budget * max_single_side_pct``.  This
        reserves budget for the other side and prevents the position from
        becoming a pure directional bet.

        Args:
            pos: The accumulating position.
            side: ``"Up"`` or ``"Down"``.
            ask_price: Fill price for cost computation.
            fill_qty: Proposed fill quantity.

        Returns:
            ``True`` if the fill would violate the single-side cap.

        """
        if side == "Up":
            this_leg = pos.up_leg
            other_leg = pos.down_leg
        else:
            this_leg = pos.down_leg
            other_leg = pos.up_leg

        # Both sides have fills — no single-side cap applies
        if other_leg.quantity > ZERO:
            return False

        # Check if adding this fill would exceed the cap
        new_cost = this_leg.cost_basis + ask_price * fill_qty
        cap = pos.budget * self.config.max_single_side_pct
        return new_cost > cap

    def _hypothetical_combined_vwap(
        self,
        pos: AccumulatingPosition,
        side: str,
        price: Decimal,
        qty: Decimal,
    ) -> Decimal:
        """Compute what the combined VWAP would be after a hypothetical fill.

        Args:
            pos: The accumulating position.
            side: ``"Up"`` or ``"Down"``.
            price: Fill price.
            qty: Fill quantity.

        Returns:
            Hypothetical combined VWAP, or zero if either side would still
            have no fills.

        """
        if side == "Up":
            up_cost = pos.up_leg.cost_basis + price * qty
            up_qty = pos.up_leg.quantity + qty
            down_cost = pos.down_leg.cost_basis
            down_qty = pos.down_leg.quantity
        else:
            up_cost = pos.up_leg.cost_basis
            up_qty = pos.up_leg.quantity
            down_cost = pos.down_leg.cost_basis + price * qty
            down_qty = pos.down_leg.quantity + qty

        if up_qty <= ZERO or down_qty <= ZERO:
            return ZERO

        up_vwap = up_cost / up_qty
        down_vwap = down_cost / down_qty
        return up_vwap + down_vwap

    def _would_exceed_imbalance(
        self,
        pos: AccumulatingPosition,
        side: str,
        fill_qty: Decimal,
    ) -> bool:
        """Check if adding a fill would exceed the max imbalance ratio.

        Args:
            pos: The accumulating position.
            side: ``"Up"`` or ``"Down"``.
            fill_qty: Proposed fill quantity.

        Returns:
            ``True`` if the fill would violate the imbalance constraint.

        """
        if side == "Up":
            this_qty = pos.up_leg.quantity + fill_qty
            other_qty = pos.down_leg.quantity
        else:
            this_qty = pos.down_leg.quantity + fill_qty
            other_qty = pos.up_leg.quantity

        # If other side has no fills yet, allow the fill (can't compute ratio)
        if other_qty <= ZERO:
            return False

        max_qty = max(this_qty, other_qty)
        min_qty = min(this_qty, other_qty)
        ratio = max_qty / min_qty
        return ratio > self.config.max_imbalance_ratio

    # ------------------------------------------------------------------
    # Settlement
    # ------------------------------------------------------------------

    async def _settle_expired_positions(self) -> None:
        """Settle accumulating positions whose market windows have expired.

        Compute P&L based on paired quantity, unpaired excess, and fees.
        Persist results via the attached repository.
        """
        now = int(time.time())
        expired = [
            cid for cid, pos in self._positions.items() if pos.opportunity.window_end_ts <= now
        ]

        for cid in expired:
            pos = self._positions.pop(cid)

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
                down_entry=pos.down_leg.entry_price,
                down_qty=pos.down_leg.quantity,
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
                "%s CLOSE %s %s pnl=%.4f cost=$%.4f"
                " combined_vwap=%.4f paired_qty=%.1f"
                " up_qty=%.1f down_qty=%.1f winning=%s asset=%s",
                mode,
                cid[:12],
                outcome_label,
                pnl,
                pos.total_cost_basis,
                pos.combined_vwap,
                pos.paired_quantity,
                pos.up_leg.quantity,
                pos.down_leg.quantity,
                winning_side,
                pos.opportunity.asset,
            )

    def _compute_pnl(self, pos: AccumulatingPosition, winning_side: str | None) -> Decimal:
        """Compute P&L for an accumulating position at settlement.

        Paired quantity (min of both legs) earns $1.00 per token.
        Unpaired excess on the winning side also earns $1.00.
        Unpaired excess on the losing side earns $0.00.
        Total fees are deducted from gross P&L.

        Args:
            pos: The accumulating position being settled.
            winning_side: ``"Up"`` or ``"Down"``, or ``None``.

        Returns:
            Realised P&L in USDC (net of fees).

        """
        up_fee = ZERO
        down_fee = ZERO
        if pos.up_leg.quantity > ZERO:
            up_fee = (
                compute_poly_fee(
                    pos.up_leg.entry_price, self.config.fee_rate, self.config.fee_exponent
                )
                * pos.up_leg.quantity
            )
        if pos.down_leg.quantity > ZERO:
            down_fee = (
                compute_poly_fee(
                    pos.down_leg.entry_price, self.config.fee_rate, self.config.fee_exponent
                )
                * pos.down_leg.quantity
            )
        total_fees = up_fee + down_fee

        # No fills on either side — no P&L
        if pos.up_leg.quantity <= ZERO and pos.down_leg.quantity <= ZERO:
            return ZERO

        # Only one side has fills (pure directional bet)
        if pos.up_leg.quantity <= ZERO or pos.down_leg.quantity <= ZERO:
            if pos.up_leg.quantity > ZERO:
                if winning_side == "Up":
                    return pos.up_leg.quantity * _WIN_PRICE - pos.up_leg.cost_basis - total_fees
                return ZERO - pos.up_leg.cost_basis
            if winning_side == "Down":
                return pos.down_leg.quantity * _WIN_PRICE - pos.down_leg.cost_basis - total_fees
            return ZERO - pos.down_leg.cost_basis

        # Both sides have fills — compute paired + unpaired P&L
        paired_qty = pos.paired_quantity

        if winning_side is None:
            # Unknown outcome: conservatively assume paired profit only
            return paired_qty * _WIN_PRICE - pos.total_cost_basis - total_fees

        winning_qty = pos.up_leg.quantity if winning_side == "Up" else pos.down_leg.quantity
        return winning_qty * _WIN_PRICE - pos.total_cost_basis - total_fees

    # ------------------------------------------------------------------
    # Outcome resolution (delegated to Binance candles)
    # ------------------------------------------------------------------

    async def _resolve_outcome_live(self, pos: AccumulatingPosition) -> str | None:
        """Resolve outcome using Polymarket resolution, falling back to Binance.

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

    async def _resolve_outcome(self, pos: AccumulatingPosition) -> str | None:
        """Determine which side won via Binance spot price movement.

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
            return "Up"
        if close_price < open_price:
            return "Down"
        return None

    # ------------------------------------------------------------------
    # Risk management
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _log_heartbeat(self) -> None:
        """Emit a heartbeat via the shared HeartbeatLogger."""
        total_pnl = sum((r.pnl for r in self._results), start=ZERO)
        scanner_markets = self._scanner.known_market_count if self._scanner else 0
        self._heartbeat.maybe_log(
            polls=self._poll_count,
            known_markets=scanner_markets,
            accumulating=sum(
                1 for p in self._positions.values() if p.state == PositionState.ACCUMULATING
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
            "SUMMARY | open=%d | closed=%d | polls=%d",
            len(self._positions),
            len(self._results),
            self._poll_count,
        )
        for cid, pos in self._positions.items():
            logger.info(
                "SUMMARY |   %s %s combined_vwap=%.4f paired=%.1f"
                " up=%.1f@%.4f down=%.1f@%.4f cost=$%.2f",
                pos.state.value.upper(),
                cid[:12],
                pos.combined_vwap,
                pos.paired_quantity,
                pos.up_leg.quantity,
                pos.up_leg.entry_price,
                pos.down_leg.quantity,
                pos.down_leg.entry_price,
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

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Public read-only properties
    # ------------------------------------------------------------------

    @property
    def positions(self) -> dict[str, AccumulatingPosition]:
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
