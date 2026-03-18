"""Pure decision engine for the spread capture strategy.

Contain all trading logic — momentum signal, hedge threshold, fill sizing,
imbalance guards, settlement P&L, and risk management — with zero I/O.
All external interactions flow through ``ExecutionPort`` and
``MarketDataPort`` protocols, making the engine testable in isolation
and runnable in live, paper, and backtest modes without modification.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING

from trading_tools.apps.spread_capture.fees import compute_poly_fee
from trading_tools.core.models import ONE, ZERO

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

    from trading_tools.core.models import Candle

    from .config import SpreadCaptureConfig
    from .ports import ExecutionPort, FillResult, MarketDataPort
    from .repository import SpreadResultRepository

logger = logging.getLogger(__name__)

_WIN_PRICE = Decimal("1.0")
_MIN_TOKEN_QTY = Decimal(5)


def _empty_accum_dict() -> dict[str, AccumulatingPosition]:
    """Return an empty dict for dataclass default_factory."""
    return {}


def _empty_result_list() -> list[SpreadResult]:
    """Return an empty list for dataclass default_factory."""
    return []


@dataclass
class SpreadEngine:
    """Pure decision engine for directional entry + opportunistic hedge.

    Contain all strategy logic from the accumulating trader: momentum
    signal computation, hedge threshold interpolation, fill quantity
    sizing, imbalance guards, settlement P&L, drawdown halts, and
    circuit breakers.  Depend only on ``ExecutionPort`` and
    ``MarketDataPort`` protocols — no direct I/O, no ``asyncio.sleep``,
    no ``time.time()`` calls.

    Attributes:
        config: Immutable strategy parameters.
        execution: Port for executing fills (live, paper, or backtest).
        market_data: Port for fetching order books, candles, and opportunities.
        mode_label: Human-readable mode label for logging (e.g. ``"PAPER"``).

    """

    config: SpreadCaptureConfig
    execution: ExecutionPort
    market_data: MarketDataPort
    mode_label: str = "PAPER"
    _positions: dict[str, AccumulatingPosition] = field(
        default_factory=_empty_accum_dict, repr=False
    )
    _results: list[SpreadResult] = field(default_factory=_empty_result_list, repr=False)
    _poll_count: int = field(default=0, repr=False)
    _repo: SpreadResultRepository | None = field(default=None, init=False, repr=False)
    _consecutive_losses: int = field(default=0, init=False, repr=False)
    _circuit_breaker_until: int = field(default=0, init=False, repr=False)
    _session_start_capital: Decimal = field(default=ZERO, init=False, repr=False)
    _high_water_mark: Decimal = field(default=ZERO, init=False, repr=False)

    def set_repo(self, repo: SpreadResultRepository) -> None:
        """Attach a database repository for persisting settled trade results.

        Args:
            repo: An initialised ``SpreadResultRepository`` instance.

        """
        self._repo = repo

    def init_capital(self) -> None:
        """Snapshot the starting capital for drawdown tracking."""
        capital = self.execution.get_capital()
        self._session_start_capital = capital
        self._high_water_mark = capital

    # ------------------------------------------------------------------
    # Poll cycle
    # ------------------------------------------------------------------

    async def poll_cycle(self, now: int) -> None:
        """Execute one scan-fill-settle cycle at the given timestamp.

        Args:
            now: Current epoch seconds (caller-controlled for backtest).

        """
        self._poll_count += 1
        await self._settle_expired_positions(now)

        opportunities = await self.market_data.get_opportunities(set(self._positions.keys()))

        for opp in opportunities:
            if len(self._positions) >= self.config.max_open_positions:
                break
            if self._check_drawdown_halt():
                break
            if self._circuit_breaker_until > now:
                break
            self._open_accumulating_position(opp, now)

        await self._attempt_fills(now)

    def _open_accumulating_position(self, opp: SpreadOpportunity, now: int) -> None:
        """Create a new accumulating position with zero-quantity legs.

        Args:
            opp: The spread opportunity to start tracking.
            now: Current epoch seconds.

        """
        budget = self._budget_for_market()

        pos = AccumulatingPosition(
            opportunity=opp,
            state=PositionState.ACCUMULATING,
            up_leg=SideLeg(side="Up", entry_price=ZERO, quantity=ZERO, cost_basis=ZERO),
            down_leg=SideLeg(side="Down", entry_price=ZERO, quantity=ZERO, cost_basis=ZERO),
            entry_time=now,
            is_paper=self.mode_label == "PAPER",
            budget=budget,
        )
        self._positions[opp.condition_id] = pos

        logger.info(
            "%s TRACK %s budget=$%.2f signal_delay=%ds asset=%s",
            self.mode_label,
            opp.condition_id[:12],
            budget,
            self.config.signal_delay_seconds,
            opp.asset,
        )

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

    def _budget_for_market(self) -> Decimal:
        """Return the per-market budget based on position sizing config.

        Returns:
            Budget in USDC for a single market position.

        """
        return self.execution.total_capital() * self.config.max_position_pct

    # ------------------------------------------------------------------
    # Fill logic
    # ------------------------------------------------------------------

    async def _attempt_fills(self, now: int) -> None:
        """Execute the three-phase fill logic on all open positions.

        Phase 0 — Signal: determine primary side from Binance momentum.
        Phase 1 — Directional entry: fill the primary side aggressively.
        Phase 2 — Opportunistic hedge: fill the secondary side when cheap.

        Args:
            now: Current epoch seconds.

        """
        for cid in list(self._positions.keys()):
            pos = self._positions.get(cid)
            if pos is None or pos.state != PositionState.ACCUMULATING:
                continue

            if self._past_fill_cutoff(pos.opportunity, now):
                continue

            # Phase 0: determine primary side from recent Binance momentum
            if pos.primary_side is None:
                pos.primary_side = await self._determine_primary_side(pos)
                logger.info(
                    "%s SIGNAL %s primary=%s asset=%s",
                    self.mode_label,
                    cid[:12],
                    pos.primary_side,
                    pos.opportunity.asset,
                )

            try:
                up_book, down_book = await self.market_data.get_order_books(
                    pos.opportunity.up_token_id,
                    pos.opportunity.down_token_id,
                )
            except Exception:
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
        """Determine the primary side by copying the whale's directional bet.

        Priority:
        1. Whale signal — if a tracked whale has BUY trades on this market,
           copy their largest side.  This is the 79%-accurate signal from
           the whale correlator analysis.
        2. Binance mean-reversion fallback — bet against recent momentum.
        3. Cheaper side from current CLOB prices.

        Args:
            pos: The position with opportunity metadata.

        Returns:
            ``"Up"`` or ``"Down"``.

        """
        # Priority 1: copy the whale
        whale_side = await self.market_data.get_whale_signal(
            pos.opportunity.condition_id,
            pos.opportunity.window_start_ts,
        )
        if whale_side is not None:
            logger.info(
                "WHALE-SIGNAL %s side=%s asset=%s",
                pos.opportunity.condition_id[:12],
                whale_side,
                pos.opportunity.asset,
            )
            return whale_side

        # Priority 2: Binance mean-reversion
        try:
            lookback_start = pos.opportunity.window_start_ts - self.config.signal_delay_seconds
            candles = await self.market_data.get_binance_candles(
                pos.opportunity.asset,
                lookback_start,
                pos.opportunity.window_start_ts,
            )
            if candles:
                direction = self._compute_momentum_signal(candles)
                if direction is not None:
                    return "Down" if direction == "Up" else "Up"
        except Exception:
            logger.debug(
                "Binance signal unavailable for %s, using price fallback",
                pos.opportunity.asset,
            )

        # Priority 3: pick the cheaper side
        return "Up" if pos.opportunity.up_price < pos.opportunity.down_price else "Down"

    @staticmethod
    def _compute_momentum_signal(candles: Sequence[Candle]) -> str | None:
        """Compute recency-weighted momentum direction from candle data.

        Each candle's return (close - open) is weighted by its position
        in the sequence: the most recent candle gets weight N, the oldest
        gets weight 1.

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
        """Compute the time-decaying hedge threshold for the secondary side.

        Linearly interpolate from ``hedge_start_threshold`` (tight early)
        to ``hedge_end_threshold`` (loose near cutoff).  No additional
        cost cap — whale data shows 37% of hedges settle at combined
        VWAP > $1.00 and they still come out ahead overall because the
        small hedge losses are far cheaper than unhedged wipeouts.

        Args:
            pos: The accumulating position (for window metadata).
            now: Current epoch seconds.

        Returns:
            Maximum ask price to accept for a secondary-side fill.

        """
        opp = pos.opportunity
        window_duration = opp.window_end_ts - opp.window_start_ts
        if window_duration <= 0:
            return self.config.hedge_end_threshold

        elapsed_pct = Decimal(str(now - opp.window_start_ts)) / Decimal(str(window_duration))
        hedge_range = self.config.max_fill_age_pct - self.config.hedge_start_pct
        if hedge_range <= ZERO:
            return self.config.hedge_end_threshold

        normalised = max(ZERO, min(ONE, (elapsed_pct - self.config.hedge_start_pct) / hedge_range))
        return (
            self.config.hedge_start_threshold
            + (self.config.hedge_end_threshold - self.config.hedge_start_threshold) * normalised
        )

    async def _try_fill_primary(
        self,
        pos: AccumulatingPosition,
        side: str,
        ask_price: Decimal,
        depth: Decimal,
        min_order_size: Decimal = _MIN_TOKEN_QTY,
    ) -> None:
        """Attempt a fill on the primary (directional) side.

        Capped by ``max_primary_price`` to avoid buying into markets
        where the outcome is already decided and prices have ballooned.
        Imbalance ratio is still checked to avoid extreme exposure.

        Args:
            pos: The accumulating position.
            side: ``"Up"`` or ``"Down"``.
            ask_price: Current best ask on the primary side.
            depth: Total visible ask depth.
            min_order_size: Per-market minimum order size from the CLOB.

        """
        if ask_price > self.config.max_primary_price:
            return

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
        """Execute a fill via the execution port and update the position leg.

        Args:
            pos: The accumulating position.
            side: ``"Up"`` or ``"Down"``.
            ask_price: Price to buy at.
            fill_qty: Quantity to buy.

        """
        token_id = pos.opportunity.up_token_id if side == "Up" else pos.opportunity.down_token_id
        result: FillResult | None = await self.execution.execute_fill(
            token_id, "BUY", ask_price, fill_qty
        )
        if result is None:
            return

        fill_price = result.price
        fill_qty = result.quantity

        leg = pos.up_leg if side == "Up" else pos.down_leg
        if result.order_id is not None:
            leg.order_ids.append(result.order_id)

        if leg.quantity == ZERO:
            leg.entry_price = fill_price
            leg.quantity = fill_qty
            leg.cost_basis = fill_price * fill_qty
        else:
            leg.add_fill(fill_price, fill_qty)

        role = "PRIMARY" if side == pos.primary_side else "HEDGE"
        logger.info(
            "%s FILL %s %s %s price=%.4f qty=%.1f vwap=%.4f total_up=%.1f total_down=%.1f",
            self.mode_label,
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
        Secondary (hedge) side always uses ``fill_size_tokens``.

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
                else max(self.config.fill_size_tokens, min_order_size)
            )
        else:
            # Hedge side: use fill_size_tokens but never below min_order_size
            qty = max(self.config.fill_size_tokens, min_order_size)

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

        if this_qty <= other_qty:
            return False

        ratio = this_qty / other_qty
        return ratio > self.config.max_imbalance_ratio

    # ------------------------------------------------------------------
    # Settlement
    # ------------------------------------------------------------------

    async def _settle_expired_positions(self, now: int) -> None:
        """Settle accumulating positions whose market windows have expired.

        Compute P&L based on paired quantity, unpaired excess, and fees.
        Persist results via the attached repository.

        Args:
            now: Current epoch seconds.

        """
        expired = [
            cid for cid, pos in self._positions.items() if pos.opportunity.window_end_ts <= now
        ]

        for cid in expired:
            pos = self._positions.pop(cid)

            winning_side = await self.market_data.resolve_outcome(pos.opportunity)
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
                current_total = self.execution.total_capital()
                self._high_water_mark = max(self._high_water_mark, current_total)
                self._consecutive_losses = 0
            elif pnl < ZERO:
                self._record_loss(now)

            outcome_label = "WIN" if pnl > ZERO else "LOSS"
            if not outcome_known:
                outcome_label = "UNKNOWN"
            logger.info(
                "%s CLOSE %s %s pnl=%.4f cost=$%.4f"
                " combined_vwap=%.4f paired_qty=%.1f"
                " up_qty=%.1f down_qty=%.1f winning=%s asset=%s",
                self.mode_label,
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

    def _record_loss(self, now: int) -> None:
        """Increment consecutive losses and activate circuit breaker if needed.

        Args:
            now: Current epoch seconds for cooldown expiry calculation.

        """
        self._consecutive_losses += 1
        threshold = self.config.circuit_breaker_losses
        if threshold > 0 and self._consecutive_losses >= threshold:
            self._circuit_breaker_until = now + self.config.circuit_breaker_cooldown
            logger.warning(
                "CIRCUIT-BREAKER triggered after %d consecutive losses, pausing for %ds",
                self._consecutive_losses,
                self.config.circuit_breaker_cooldown,
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

    @property
    def total_pnl(self) -> Decimal:
        """Return the total P&L across all settled results."""
        return sum((r.pnl for r in self._results), start=ZERO)

    @property
    def high_water_mark(self) -> Decimal:
        """Return the session high water mark for drawdown tracking."""
        return self._high_water_mark
