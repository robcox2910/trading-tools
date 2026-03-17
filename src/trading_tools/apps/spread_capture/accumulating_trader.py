"""Directional entry + opportunistic hedge trading engine.

Implement a whale-inspired strategy that uses a Binance momentum signal
to make a directional bet on one side of an Up/Down market, then
opportunistically hedges the other side when its ask price dips below a
time-decaying threshold.

Three-phase fill logic:
  Phase 0 — Signal: on first poll, look back ``signal_delay_seconds``
      of Binance 1-min candles to determine primary direction.
  Phase 1 — Directional entry: fill the primary side aggressively with
      no price threshold.
  Phase 2 — Opportunistic hedge: fill the secondary side only when
      ``ask < hedge_threshold(t)``, where the threshold linearly
      interpolates from ``hedge_start_threshold`` (tight, early) to
      ``hedge_end_threshold`` (loose, near cutoff).
  Phase 3 — Cutoff: stop all fills after ``max_fill_age_pct``.
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
from trading_tools.core.models import ONE, ZERO, Candle, Interval
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
    from collections.abc import Sequence

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
    """Directional entry + opportunistic hedge engine for Polymarket Up/Down markets.

    Use a Binance momentum signal to determine the primary side, fill it
    aggressively, then hedge the secondary side only when its ask dips
    below a time-decaying threshold.  Imbalance ratio guards against
    extreme directional exposure.

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
            " signal_delay=%ds hedge=%.2f→%.2f max_imbal=%.1f"
            " fill_size=%s max_open=%d slugs=%s",
            mode,
            self.config.poll_interval,
            capital,
            self.config.signal_delay_seconds,
            self.config.hedge_start_threshold,
            self.config.hedge_end_threshold,
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

        # Scan for new markets to track — pass ONE to accept all active
        # markets; fill decisions are made in _attempt_fills.
        opportunities = await self._scanner.scan_per_side(
            set(self._positions.keys()),
            ONE,
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
            "%s TRACK %s budget=$%.2f signal_delay=%ds asset=%s",
            mode,
            opp.condition_id[:12],
            budget,
            self.config.signal_delay_seconds,
            opp.asset,
        )

    # ------------------------------------------------------------------
    # Fill logic
    # ------------------------------------------------------------------

    async def _attempt_fills(self) -> None:
        """Execute the three-phase fill logic on all open positions.

        Phase 0 — Signal: on first poll, look back at recent Binance
            candles to determine the primary side.
        Phase 1 — Directional entry: fill the primary side aggressively.
        Phase 2 — Opportunistic hedge: fill the secondary side only when
            ``ask < hedge_threshold(t)``.
        """
        if self.client is None:
            return

        now = int(time.time())

        for cid in list(self._positions.keys()):
            pos = self._positions.get(cid)
            if pos is None or pos.state != PositionState.ACCUMULATING:
                continue

            if self._past_fill_cutoff(pos.opportunity, now):
                continue

            # Phase 0: determine primary side from recent Binance momentum
            if pos.primary_side is None:
                pos.primary_side = await self._determine_primary_side(pos)
                mode = "LIVE" if self.live else "PAPER"
                logger.info(
                    "%s SIGNAL %s primary=%s asset=%s",
                    mode,
                    cid[:12],
                    pos.primary_side,
                    pos.opportunity.asset,
                )

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
            min_size = min(up_book.min_order_size, down_book.min_order_size)

            # Resolve primary / secondary book data
            if pos.primary_side == "Up":
                primary_ask, primary_depth = up_ask, up_depth
                secondary_ask, secondary_depth = down_ask, down_depth
            else:
                primary_ask, primary_depth = down_ask, down_depth
                secondary_ask, secondary_depth = up_ask, up_depth
            secondary_side = "Down" if pos.primary_side == "Up" else "Up"

            # Phase 1: fill primary side (no price threshold)
            if primary_ask is not None:
                await self._try_fill_primary(
                    pos, pos.primary_side, primary_ask, primary_depth, min_size
                )

            # Phase 2: opportunistic hedge on secondary side
            if secondary_ask is not None:
                hedge_threshold = self._compute_hedge_threshold(pos, now)
                if secondary_ask < hedge_threshold:
                    await self._try_fill_secondary(
                        pos, secondary_side, secondary_ask, secondary_depth, min_size
                    )

    async def _determine_primary_side(self, pos: AccumulatingPosition) -> str:
        """Determine the primary side from recent Binance momentum.

        Look back ``signal_delay_seconds`` of 1-min candles before the
        market window opened.  Compute a recency-weighted momentum score
        where each candle's contribution is weighted by its position
        (most recent candle has weight N, oldest has weight 1).  This
        gives more influence to the latest price action.

        Args:
            pos: The position with opportunity metadata.

        Returns:
            ``"Up"`` or ``"Down"``.

        """
        if self._binance is not None:
            try:
                provider = BinanceCandleProvider(self._binance)
                lookback_start = pos.opportunity.window_start_ts - self.config.signal_delay_seconds
                candles = await provider.get_candles(
                    pos.opportunity.asset,
                    Interval.M1,
                    lookback_start,
                    pos.opportunity.window_start_ts,
                )
                if candles:
                    direction = self._compute_momentum_signal(candles)
                    if direction is not None:
                        return direction
            except (BinanceError, httpx.HTTPError, KeyError, ValueError):
                logger.debug(
                    "Binance signal unavailable for %s, using price fallback",
                    pos.opportunity.asset,
                )

        # Fallback: pick the cheaper side from the opportunity
        return "Up" if pos.opportunity.up_price < pos.opportunity.down_price else "Down"

    @staticmethod
    def _compute_momentum_signal(candles: Sequence[Candle]) -> str | None:
        """Compute recency-weighted momentum direction from candle data.

        Each candle's return (close - open) is weighted by its position
        in the sequence: the most recent candle gets weight N (where N
        is the number of candles), the second most recent gets N-1, etc.
        This ensures recent price action dominates the signal.

        Args:
            candles: List of 1-min candles ordered oldest to newest.

        Returns:
            ``"Up"`` if weighted momentum is positive, ``"Down"`` if
            negative, ``None`` if exactly flat.

        """
        weighted_sum = ZERO
        for i, candle in enumerate(candles):
            weight = Decimal(i + 1)
            weighted_sum += weight * (candle.close - candle.open)

        if weighted_sum > ZERO:
            return "Up"
        if weighted_sum < ZERO:
            return "Down"
        return None

    def _compute_hedge_threshold(self, pos: AccumulatingPosition, now: int) -> Decimal:
        """Compute the hedge threshold for the secondary side.

        Use the tighter of two caps:
        1. Time-decaying threshold: linearly interpolate from
           ``hedge_start_threshold`` to ``hedge_end_threshold``.
        2. Cost cap: ``1.0 - primary_vwap - min_spread_margin`` so the
           combined VWAP stays profitable after fees.

        Args:
            pos: The accumulating position (for primary VWAP).
            now: Current epoch seconds.

        Returns:
            Maximum ask price to accept for a secondary-side fill.

        """
        opp = pos.opportunity
        window_duration = opp.window_end_ts - opp.window_start_ts
        if window_duration <= 0:
            time_threshold = self.config.hedge_end_threshold
        else:
            elapsed_pct = Decimal(str(now - opp.window_start_ts)) / Decimal(str(window_duration))
            hedge_range = self.config.max_fill_age_pct - self.config.hedge_start_pct
            if hedge_range <= ZERO:
                time_threshold = self.config.hedge_end_threshold
            else:
                normalised = max(
                    ZERO, min(ONE, (elapsed_pct - self.config.hedge_start_pct) / hedge_range)
                )
                time_threshold = (
                    self.config.hedge_start_threshold
                    + (self.config.hedge_end_threshold - self.config.hedge_start_threshold)
                    * normalised
                )

        # Dynamic cap: don't let combined VWAP exceed 1.0 - margin
        primary_leg = pos.up_leg if pos.primary_side == "Up" else pos.down_leg
        if primary_leg.quantity > ZERO:
            cost_cap = ONE - primary_leg.entry_price - self.config.min_spread_margin
        else:
            cost_cap = self.config.hedge_end_threshold

        return min(time_threshold, cost_cap)

    async def _try_fill_primary(
        self,
        pos: AccumulatingPosition,
        side: str,
        ask_price: Decimal,
        depth: Decimal,
        min_order_size: Decimal = _MIN_TOKEN_QTY,
    ) -> None:
        """Attempt a fill on the primary (directional) side.

        No price threshold — the primary side is filled aggressively.
        Imbalance ratio is still checked to avoid extreme exposure.

        Args:
            pos: The accumulating position.
            side: ``"Up"`` or ``"Down"``.
            ask_price: Current best ask on the primary side.
            depth: Total visible ask depth.
            min_order_size: Per-market minimum order size from the CLOB.

        """
        fill_qty = self._compute_fill_qty(
            pos, side, ask_price, depth, min_order_size, is_primary=True
        )
        if fill_qty is None:
            return

        if self._would_exceed_imbalance(pos, side, fill_qty):
            return

        await self._execute_fill(pos, side, ask_price, fill_qty)

    async def _try_fill_secondary(
        self,
        pos: AccumulatingPosition,
        side: str,
        ask_price: Decimal,
        depth: Decimal,
        min_order_size: Decimal = _MIN_TOKEN_QTY,
    ) -> None:
        """Attempt a fill on the secondary (hedge) side.

        The caller is responsible for checking the hedge threshold.
        Imbalance ratio is still checked here.

        Args:
            pos: The accumulating position.
            side: ``"Up"`` or ``"Down"``.
            ask_price: Current best ask on the secondary side.
            depth: Total visible ask depth.
            min_order_size: Per-market minimum order size from the CLOB.

        """
        fill_qty = self._compute_fill_qty(
            pos, side, ask_price, depth, min_order_size, is_primary=False
        )
        if fill_qty is None:
            return

        if self._would_exceed_imbalance(pos, side, fill_qty):
            return

        await self._execute_fill(pos, side, ask_price, fill_qty)

    async def _execute_fill(
        self,
        pos: AccumulatingPosition,
        side: str,
        ask_price: Decimal,
        fill_qty: Decimal,
    ) -> None:
        """Execute a fill (paper or live) and update the position leg.

        Args:
            pos: The accumulating position.
            side: ``"Up"`` or ``"Down"``.
            ask_price: Price to buy at.
            fill_qty: Quantity to buy.

        """
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
            leg.entry_price = fill_price
            leg.quantity = fill_qty
            leg.cost_basis = fill_price * fill_qty
        else:
            leg.add_fill(fill_price, fill_qty)

        role = "PRIMARY" if side == pos.primary_side else "HEDGE"
        mode = "LIVE" if self.live else "PAPER"
        logger.info(
            "%s FILL %s %s %s price=%.4f qty=%.1f vwap=%.4f total_up=%.1f total_down=%.1f",
            mode,
            pos.opportunity.condition_id[:12],
            role,
            side,
            fill_price,
            fill_qty,
            leg.entry_price,
            pos.up_leg.quantity,
            pos.down_leg.quantity,
        )

    def _compute_fill_qty(
        self,
        pos: AccumulatingPosition,
        side: str,
        ask_price: Decimal,
        depth: Decimal,
        min_order_size: Decimal = _MIN_TOKEN_QTY,
        *,
        is_primary: bool = True,
    ) -> Decimal | None:
        """Compute the fill quantity, clamped by depth, budget, and minimum.

        Primary side uses ``initial_fill_size`` then ``fill_size_tokens``.
        Secondary (hedge) side always uses ``fill_size_tokens`` to DCA into
        the position gradually, mimicking whale behaviour of many small
        hedge fills.

        Args:
            pos: The accumulating position.
            side: ``"Up"`` or ``"Down"``.
            ask_price: Current best ask price.
            depth: Total visible ask depth.
            min_order_size: Per-market minimum from the CLOB order book.
            is_primary: ``True`` for directional side, ``False`` for hedge.

        Returns:
            Fill quantity in tokens, or ``None`` if below minimum.

        """
        leg = pos.up_leg if side == "Up" else pos.down_leg

        if is_primary:
            qty = (
                self.config.initial_fill_size
                if leg.quantity == ZERO
                else self.config.fill_size_tokens
            )
        else:
            # Hedge side: always small incremental fills (whale DCA pattern)
            qty = self.config.fill_size_tokens

        max_from_depth = depth * self.config.max_book_pct
        qty = min(qty, max_from_depth)

        budget_remaining = pos.budget - pos.total_cost_basis
        if budget_remaining <= ZERO:
            return None
        max_from_budget = (budget_remaining / ask_price).quantize(Decimal("0.01"))
        qty = min(qty, max_from_budget)

        qty = qty.quantize(Decimal("0.01"))
        if qty < min_order_size:
            return None

        return qty

    def _past_fill_cutoff(self, opp: SpreadOpportunity, now: int) -> bool:
        """Return ``True`` when the market window is past the fill cutoff.

        Args:
            opp: The spread opportunity with window timestamps.
            now: Current epoch seconds.

        Returns:
            ``True`` if fills should be stopped for this market.

        """
        window_duration = opp.window_end_ts - opp.window_start_ts
        if window_duration <= 0:
            return True
        elapsed_pct = Decimal(str(now - opp.window_start_ts)) / Decimal(str(window_duration))
        return elapsed_pct > self.config.max_fill_age_pct

    def _would_exceed_imbalance(
        self,
        pos: AccumulatingPosition,
        side: str,
        fill_qty: Decimal,
    ) -> bool:
        """Check if adding a fill would worsen imbalance beyond the limit.

        Always allow fills on the lighter side (they improve the ratio).
        Only block fills on the heavier side that would push the ratio
        further above the limit.

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

        if other_qty <= ZERO:
            return False

        # Always allow fills on the lighter side — they improve balance
        if this_qty <= other_qty:
            return False

        ratio = this_qty / other_qty
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
