"""Pure decision engine for the directional trading algorithm.

Contain all trading logic — feature extraction, probability estimation,
Kelly sizing, entry timing, settlement, and risk management — with zero
I/O.  All external interactions flow through ``ExecutionPort`` and
``MarketDataPort`` protocols, making the engine testable in isolation
and runnable in live, paper, and backtest modes without modification.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any, cast

from trading_tools.apps.spread_capture.fees import compute_poly_fee
from trading_tools.core.models import ONE, ZERO

from .features import extract_features
from .kelly import kelly_fraction
from .models import (
    DirectionalPosition,
    DirectionalResult,
    DirectionalResultRecord,
    MarketOpportunity,
)

if TYPE_CHECKING:
    from .config import DirectionalConfig
    from .estimator import ProbabilityEstimator
    from .ports import ExecutionPort, MarketDataPort
    from .repository import DirectionalResultRepository

logger = logging.getLogger(__name__)

_WIN_PAYOUT = Decimal("1.0")
_MIN_TOKEN_QTY = Decimal(5)
_HALF = Decimal("0.5")


def _empty_positions() -> dict[str, DirectionalPosition]:
    """Create an empty positions dictionary."""
    return {}


def _empty_results() -> list[DirectionalResult]:
    """Create an empty results list."""
    return []


def _empty_estimator_by_slug() -> dict[str, ProbabilityEstimator]:
    """Create an empty estimator-by-slug dictionary."""
    return {}


@dataclass
class DirectionalEngine:
    """Pure decision engine for directional binary market trading.

    Scan active markets, filter by entry timing window, extract features
    from Binance candles and Polymarket order books, estimate P(Up) via
    an injectable estimator, size positions via Kelly criterion, execute
    fills through the execution port, and settle expired positions.

    All I/O is abstracted behind ``ExecutionPort`` and ``MarketDataPort``
    protocols.  The engine maintains no persistent state beyond in-memory
    position tracking and session-level risk metrics.

    Attributes:
        config: Algorithm configuration.
        execution: Fill execution adapter.
        market_data: Market data source adapter.
        estimator: Probability estimator (features → P(Up)).  Used as
            fallback when no slug-specific estimator exists.
        mode_label: ``"paper"`` or ``"live"`` for logging.
        estimator_by_slug: Per-series-slug estimators with slug-specific
            weights.  Falls back to ``estimator`` for unknown slugs.

    """

    config: DirectionalConfig
    execution: ExecutionPort
    market_data: MarketDataPort
    estimator: ProbabilityEstimator
    mode_label: str = "paper"
    estimator_by_slug: dict[str, ProbabilityEstimator] = field(
        default_factory=_empty_estimator_by_slug,
    )
    _positions: dict[str, DirectionalPosition] = field(default_factory=_empty_positions)
    _results: list[DirectionalResult] = field(default_factory=_empty_results)
    _poll_count: int = 0
    _repo: DirectionalResultRepository | None = None
    _consecutive_losses: int = 0
    _circuit_breaker_until: int = 0
    _session_start_capital: Decimal = ZERO
    _high_water_mark: Decimal = ZERO

    def __post_init__(self) -> None:
        """Initialize session-level capital tracking."""
        cap = self.execution.total_capital()
        if self._session_start_capital == ZERO:
            self._session_start_capital = cap
        if self._high_water_mark == ZERO:
            self._high_water_mark = cap

    @property
    def positions(self) -> dict[str, DirectionalPosition]:
        """Return the current open positions."""
        return self._positions

    @property
    def results(self) -> list[DirectionalResult]:
        """Return all settled results this session."""
        return self._results

    def set_repository(self, repo: DirectionalResultRepository) -> None:
        """Attach a repository for persisting trade results.

        Args:
            repo: Async repository for directional results.

        """
        self._repo = repo

    async def poll_cycle(self, now: int) -> None:
        """Run one scan-evaluate-settle cycle.

        1. Settle any expired positions.
        2. Check risk gates (drawdown halt, circuit breaker).
        3. Scan for active markets.
        4. For each market in the entry window, evaluate and enter.

        Args:
            now: Current UTC epoch seconds.

        """
        self._poll_count += 1

        await self._settle_expired(now)

        if self._check_drawdown_halt():
            return
        if self._check_circuit_breaker(now):
            return

        open_cids = set(self._positions.keys())
        if len(open_cids) >= self.config.max_open_positions:
            return

        markets = await self.market_data.get_active_markets(open_cids)

        for market in markets:
            if len(self._positions) >= self.config.max_open_positions:
                break
            if self._in_entry_window(market, now):
                await self._evaluate_and_enter(market, now)

    def _in_entry_window(self, market: MarketOpportunity, now: int) -> bool:
        """Check whether the current time is within the entry window.

        Entry is allowed when the market closes in ``[entry_window_end,
        entry_window_start]`` seconds from now.

        Args:
            market: Market to check.
            now: Current epoch seconds.

        Returns:
            ``True`` if now is within the entry window.

        """
        time_to_close = market.window_end_ts - now
        return self.config.entry_window_end <= time_to_close <= self.config.entry_window_start

    async def _evaluate_and_enter(self, market: MarketOpportunity, now: int) -> None:
        """Extract features, estimate probability, size and execute a fill.

        Skip if the edge is below ``min_edge`` or Kelly says no bet.

        Args:
            market: Market opportunity to evaluate.
            now: Current epoch seconds.

        """
        lookback_start = now - self.config.signal_lookback_seconds
        candles = await self.market_data.get_binance_candles(market.asset, lookback_start, now)
        if len(candles) < _MIN_CANDLES_REQUIRED:
            logger.debug("Skipping %s: only %d candles", market.condition_id[:12], len(candles))
            return

        up_book, down_book = await self.market_data.get_order_books(
            market.up_token_id, market.down_token_id
        )

        whale_direction = await self.market_data.get_whale_signal(market.condition_id)

        features = extract_features(candles, up_book, down_book, whale_direction=whale_direction)
        est = (
            self.estimator_by_slug.get(market.series_slug, self.estimator)
            if market.series_slug
            else self.estimator
        )
        p_up = est.estimate(features)

        # Determine predicted side and the relevant token/price
        if p_up >= _HALF:
            predicted_side = "Up"
            p_win = p_up
            token_id = market.up_token_id
            token_price = market.up_price
        else:
            predicted_side = "Down"
            p_win = ONE - p_up
            token_id = market.down_token_id
            token_price = market.down_price

        if token_price < self.config.min_token_price:
            logger.debug(
                "Skipping %s: token_price %.4f < min %.4f (market already decided)",
                market.condition_id[:12],
                token_price,
                self.config.min_token_price,
            )
            return

        edge = p_win - token_price
        if edge < self.config.min_edge:
            logger.debug(
                "Skipping %s: edge %.4f < min_edge %.4f",
                market.condition_id[:12],
                edge,
                self.config.min_edge,
            )
            return

        # Kelly sizing
        fraction = kelly_fraction(
            p_win=p_win,
            token_price=token_price,
            fractional=self.config.kelly_fraction,
            max_fraction=self.config.max_position_pct,
        )
        if fraction <= ZERO:
            return

        budget = self.execution.total_capital() * fraction
        fee_per_token = compute_poly_fee(
            token_price, self.config.fee_rate, self.config.fee_exponent
        )
        cost_per_token = token_price + fee_per_token
        if cost_per_token <= ZERO:
            return

        quantity = (budget / cost_per_token).quantize(Decimal("0.01"))
        if quantity < _MIN_TOKEN_QTY:
            logger.debug(
                "Skipping %s: quantity %.2f < min %s",
                market.condition_id[:12],
                quantity,
                _MIN_TOKEN_QTY,
            )
            return

        fill = await self.execution.execute_fill(token_id, "BUY", token_price, quantity)
        if fill is None:
            return

        total_fee = (
            compute_poly_fee(fill.price, self.config.fee_rate, self.config.fee_exponent)
            * fill.quantity
        )
        cost_basis = fill.price * fill.quantity + total_fee

        position = DirectionalPosition(
            opportunity=market,
            predicted_side=predicted_side,
            p_up=p_up,
            token_id=token_id,
            entry_price=fill.price,
            quantity=fill.quantity,
            cost_basis=cost_basis,
            fee=total_fee,
            entry_time=now,
            features=features,
            order_id=fill.order_id,
            is_paper=self.mode_label == "paper",
        )
        self._positions[market.condition_id] = position

        logger.info(
            "[%s] OPEN %s %s | p_up=%.3f p_win=%.3f edge=%.3f | qty=%.1f @ $%.4f | cost=$%.4f",
            self.mode_label.upper(),
            predicted_side,
            market.asset,
            p_up,
            p_win,
            edge,
            fill.quantity,
            fill.price,
            cost_basis,
        )

    async def _settle_expired(self, now: int) -> None:
        """Settle positions whose market window has closed.

        For each expired position, resolve the outcome, compute P&L,
        record the result, and remove from active positions.

        Args:
            now: Current epoch seconds.

        """
        expired_cids = [
            cid for cid, pos in self._positions.items() if now >= pos.opportunity.window_end_ts
        ]
        for cid in expired_cids:
            pos = self._positions.pop(cid)
            winning_side = await self.market_data.resolve_outcome(pos.opportunity)

            if winning_side is None:
                logger.warning("Could not resolve outcome for %s", cid[:12])
                pnl = -pos.cost_basis
            elif winning_side == pos.predicted_side:
                # Win: token pays $1.00
                pnl = pos.quantity * (_WIN_PAYOUT - pos.entry_price) - pos.fee
            else:
                # Loss: token pays $0.00
                pnl = -(pos.entry_price * pos.quantity + pos.fee)

            result = DirectionalResult(
                opportunity=pos.opportunity,
                predicted_side=pos.predicted_side,
                winning_side=winning_side,
                p_up=pos.p_up,
                token_id=pos.token_id,
                entry_price=pos.entry_price,
                quantity=pos.quantity,
                cost_basis=pos.cost_basis,
                fee=pos.fee,
                entry_time=pos.entry_time,
                settled_at=now,
                pnl=pnl,
                features=pos.features,
                is_paper=pos.is_paper,
                order_id=pos.order_id,
            )
            self._results.append(result)

            if pnl >= ZERO:
                self._consecutive_losses = 0
            else:
                self._record_loss(now)

            if self.config.compound_profits and hasattr(self.execution, "add_capital"):
                cast("Any", self.execution).add_capital(pos.cost_basis + pnl)

            available = self.execution.get_capital()
            self._high_water_mark = max(self._high_water_mark, available)

            logger.info(
                "[%s] CLOSE %s %s | winner=%s | pnl=$%.4f | capital=$%.2f",
                self.mode_label.upper(),
                pos.predicted_side,
                pos.opportunity.asset,
                winning_side,
                pnl,
                available,
            )

            if self._repo is not None:
                record = DirectionalResultRecord.from_result(result)
                await self._repo.save_result(record)

    def _check_drawdown_halt(self) -> bool:
        """Halt new entries if session drawdown exceeds the maximum.

        Returns:
            ``True`` if entries should be halted.

        """
        if self._session_start_capital <= ZERO:
            return False
        current = self.execution.total_capital()
        drawdown = (self._high_water_mark - current) / self._session_start_capital
        if drawdown >= self.config.max_drawdown_pct:
            logger.warning(
                "Drawdown halt: %.1f%% drawdown exceeds %.1f%% limit",
                drawdown * Decimal(100),
                self.config.max_drawdown_pct * Decimal(100),
            )
            return True
        return False

    def _check_circuit_breaker(self, now: int) -> bool:
        """Check whether the circuit breaker is active.

        Args:
            now: Current epoch seconds.

        Returns:
            ``True`` if the circuit breaker is cooling down.

        """
        return self._circuit_breaker_until > now

    def _record_loss(self, now: int) -> None:
        """Increment consecutive losses and trigger circuit breaker if needed.

        Args:
            now: Current epoch seconds.

        """
        self._consecutive_losses += 1
        if (
            self.config.circuit_breaker_losses > 0
            and self._consecutive_losses >= self.config.circuit_breaker_losses
        ):
            self._circuit_breaker_until = now + self.config.circuit_breaker_cooldown
            logger.warning(
                "Circuit breaker: %d consecutive losses, pausing until %d",
                self._consecutive_losses,
                self._circuit_breaker_until,
            )
            self._consecutive_losses = 0


_MIN_CANDLES_REQUIRED = 16
