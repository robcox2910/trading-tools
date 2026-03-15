"""Core copy-trading engine that mirrors whale bets in real-time.

Run a tight polling loop that detects whale directional bias signals and
either simulates (paper) or executes (live) temporal spread arbitrage
trades on Polymarket.

Temporal spread arbitrage:
- Phase 1 (directional entry): copy the whale's favoured side immediately
  at current CLOB prices.
- Phase 2 (hedge): monitor the opposite side's price each poll cycle.
  When leg1_price + hedge_price < max_spread_cost, place the hedge order
  to lock in guaranteed profit regardless of outcome.
- If no hedge opportunity arises before expiry, the position resolves
  directionally (profitable when the whale is correct).

Position lifecycle:
- OPEN: first time a market signals above threshold — buy leg 1.
- HEDGE: opposite side becomes cheap enough — buy leg 2, lock in profit.
- CLOSE: market window expires — resolve P&L via Binance spot prices.

Performance design:
- 5-second default poll interval for minimal latency on 5-minute markets.
- Incremental signal detection (see ``SignalDetector``).
- Pre-authenticated client: Polymarket connection established at startup.
- GTC limit orders by default: better fills on thin books.
- Async throughout: no blocking calls in the hot path.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING

from trading_tools.apps.bot_framework.balance_manager import BalanceManager
from trading_tools.apps.bot_framework.heartbeat import HeartbeatLogger
from trading_tools.apps.bot_framework.order_executor import OrderExecutor
from trading_tools.apps.bot_framework.redeemer import PositionRedeemer
from trading_tools.apps.bot_framework.shutdown import GracefulShutdown
from trading_tools.clients.binance.client import BinanceClient
from trading_tools.core.models import ZERO, Interval
from trading_tools.data.providers.binance import BinanceCandleProvider

from .models import CopyResult, CopyResultRecord, CopySignal, OpenPosition, PositionState, SideLeg
from .signal_detector import SignalDetector

if TYPE_CHECKING:
    from trading_tools.clients.polymarket.client import PolymarketClient
    from trading_tools.clients.polymarket.models import MarketToken

    from .config import WhaleCopyConfig
    from .repository import CopyResultRepository

logger = logging.getLogger(__name__)

_BALANCE_REFRESH_POLLS = 60
_WIN_PRICE = Decimal("1.0")
_MIN_TOKEN_QTY = Decimal(5)
_ONE = Decimal(1)


def _empty_position_dict() -> dict[str, OpenPosition]:
    """Return an empty dict[str, OpenPosition] for dataclass default_factory."""
    return {}


def _empty_result_list() -> list[CopyResult]:
    """Return an empty list[CopyResult] for dataclass default_factory."""
    return []


@dataclass
class WhaleCopyTrader:
    """Copy-trading engine that mirrors whale bets on Polymarket.

    Poll the Polymarket Data API directly for new whale trades, detect
    directional bias signals, and either log virtual trades (paper mode)
    or place real orders (live mode) via the Polymarket CLOB API.

    Use a two-phase temporal spread arbitrage approach: enter
    directionally on the whale's favoured side, then hedge the opposite
    side when the spread is favourable enough to lock in profit.

    Attributes:
        config: Immutable service configuration.
        live: Enable live trading (requires ``client``).
        client: Authenticated Polymarket client for CLOB data and live orders.

    """

    config: WhaleCopyConfig
    live: bool = False
    client: PolymarketClient | None = None
    _detector: SignalDetector | None = field(default=None, repr=False)
    _binance: BinanceClient | None = field(default=None, repr=False)
    _positions: dict[str, OpenPosition] = field(default_factory=_empty_position_dict, repr=False)
    _results: list[CopyResult] = field(default_factory=_empty_result_list, repr=False)
    _poll_count: int = field(default=0, repr=False)
    _shutdown: GracefulShutdown = field(default_factory=GracefulShutdown, init=False, repr=False)
    _heartbeat: HeartbeatLogger = field(default_factory=HeartbeatLogger, init=False, repr=False)
    _redeemer: PositionRedeemer | None = field(default=None, init=False, repr=False)
    _executor: OrderExecutor | None = field(default=None, init=False, repr=False)
    _balance_manager: BalanceManager | None = field(default=None, init=False, repr=False)
    _repo: CopyResultRepository | None = field(default=None, init=False, repr=False)
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

        Initialise the signal detector and enter a tight async loop that
        polls for signals, checks hedge opportunities, and closes expired
        positions. Log a heartbeat every 60 seconds for monitoring.
        """
        self._detector = SignalDetector(
            whale_address=self.config.whale_address,
            min_bias=self.config.min_bias,
            min_trades=self.config.min_trades,
            lookback_seconds=self.config.lookback_seconds,
            min_time_to_start=self.config.min_time_to_start,
            max_window_seconds=self.config.max_window_seconds,
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
            "whale-copy started mode=%s address=%s poll=%ds"
            " lookback=%ds min_bias=%.1f min_trades=%d"
            " min_time_to_start=%ds capital=$%s max_pos=%s%%"
            " max_spread_cost=%.2f max_entry_price=%.2f"
            " hedge_market=%s def_hedge=%.0f%% kelly=%.0f%%x%.1f"
            " take_profit=%.0f%% fee_rate=%.4f max_unhedged=%.0f%%",
            mode,
            self.config.whale_address,
            self.config.poll_interval,
            self.config.lookback_seconds,
            self.config.min_bias,
            self.config.min_trades,
            self.config.min_time_to_start,
            capital,
            self.config.max_position_pct * 100,
            self.config.max_spread_cost,
            self.config.max_entry_price,
            self.config.hedge_with_market_orders,
            self.config.defensive_hedge_pct * 100,
            self.config.win_rate * 100,
            self.config.kelly_fraction,
            self.config.take_profit_pct * 100,
            self.config.clob_fee_rate,
            self.config.max_unhedged_exposure_pct * 100,
        )

        try:
            while not self._shutdown.should_stop:
                await self._poll_cycle()
                self._log_heartbeat()
                await asyncio.sleep(self.config.poll_interval)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            self._log_summary()
            await self._binance.close()
            await self._detector.close()

    def set_repo(self, repo: CopyResultRepository) -> None:
        """Attach a database repository for persisting closed trade results.

        Args:
            repo: An initialised ``CopyResultRepository`` instance.

        """
        self._repo = repo

    def stop(self) -> None:
        """Signal the polling loop to stop after the current cycle."""
        self._shutdown.request()

    @property
    def _committed_capital(self) -> Decimal:
        """Return the total cost basis of all open positions.

        Sum the ``total_cost_basis`` of every open position to determine
        how much capital is currently locked up.

        Returns:
            Total committed capital in USDC.

        """
        return sum(
            (pos.total_cost_basis for pos in self._positions.values()),
            start=ZERO,
        )

    def _get_capital(self) -> Decimal:
        """Return the current capital available for position sizing.

        In live mode, use the real USDC balance from the shared
        ``BalanceManager`` (already excludes capital locked in positions).
        In paper mode, start from base capital, optionally add realised
        P&L (compound mode), and subtract committed capital.

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

        Unlike ``_get_capital`` which reflects *available* capital, this
        returns the total including committed capital. Used for computing
        per-asset concentration limits.

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
        """Execute one poll-detect-act cycle.

        Refresh the live balance periodically, detect new signals, open
        leg 1 for new markets, check hedge opportunities for unhedged
        positions, and close expired ones.
        """
        assert self._detector is not None  # noqa: S101
        self._poll_count += 1

        if (
            self.live
            and self._balance_manager is not None
            and self._poll_count % _BALANCE_REFRESH_POLLS == 0
        ):
            await self._balance_manager.refresh()

        signals = await self._detector.detect_signals()
        for signal in signals:
            await self._process_signal(signal)

        price_cache = await self._build_price_cache()
        await self._check_take_profits(price_cache)
        await self._check_defensive_hedges(price_cache)
        await self._check_hedge_opportunities(price_cache)
        await self._close_expired_positions()

        if self._redeemer is not None:
            await self._redeemer.redeem_if_available()

    async def _process_signal(self, signal: CopySignal) -> None:
        """Open a directional entry for new markets, ignore existing ones.

        Skip signals during a circuit-breaker cooldown period, when session
        drawdown exceeds the limit, when the market window is too far elapsed,
        or when the adaptive win rate drops below the halt threshold.

        Args:
            signal: The copy signal to process.

        """
        cid = signal.condition_id
        if cid in self._positions:
            return

        now = int(time.time())

        # Max drawdown kill-switch
        if self._check_drawdown_halt():
            logger.info("  DRAWDOWN-HALT active, skipping %s", cid[:12])
            return

        if self._circuit_breaker_until > now:
            logger.info(
                "  CIRCUIT-BREAKER active for %ds, skipping %s",
                self._circuit_breaker_until - now,
                cid[:12],
            )
            return

        # Max entry age filter
        if self.config.max_entry_age_pct > ZERO:
            window_duration = signal.window_end_ts - signal.window_start_ts
            if window_duration > 0:
                elapsed_pct = Decimal(str(now - signal.window_start_ts)) / Decimal(
                    str(window_duration)
                )
                if elapsed_pct > self.config.max_entry_age_pct:
                    logger.info(
                        "  SKIP %s: window %.0f%% elapsed > max %.0f%%",
                        cid[:12],
                        elapsed_pct * 100,
                        self.config.max_entry_age_pct * 100,
                    )
                    return

        # Win rate halt
        if self.config.halt_win_rate > ZERO:
            adaptive_rate = self._effective_win_rate()
            if adaptive_rate < self.config.halt_win_rate:
                logger.info(
                    "  WIN-RATE-HALT: adaptive=%.2f < halt=%.2f, skipping %s",
                    adaptive_rate,
                    self.config.halt_win_rate,
                    cid[:12],
                )
                return

        await self._open_position(signal)

    def _effective_win_rate(self) -> Decimal:
        """Compute the effective win rate for Kelly position sizing.

        When adaptive Kelly is enabled and enough closed unhedged trades
        exist, compute the rolling win rate from realised outcomes.
        Only count trades with ``outcome_known=True`` and unhedged state
        (hedged trades always win, skewing the rate). Floor at
        ``min_win_rate`` to prevent collapse after short losing streaks.

        Returns:
            Win rate as a ``Decimal`` between ``min_win_rate`` and ``1.0``.

        """
        if not self.config.adaptive_kelly:
            return self.config.win_rate

        eligible = [
            r for r in self._results if r.outcome_known and r.state == PositionState.UNHEDGED
        ]

        if len(eligible) < self.config.min_kelly_results:
            return self.config.win_rate

        wins = sum(1 for r in eligible if r.pnl > ZERO)
        computed = Decimal(wins) / Decimal(len(eligible))
        return max(computed, self.config.min_win_rate)

    def _check_drawdown_halt(self) -> bool:
        """Return ``True`` when session drawdown exceeds the configured limit.

        Compare cumulative P&L against the session start capital. When the
        loss exceeds ``max_drawdown_pct`` of starting capital, halt all new
        entries until the session is restarted.

        Returns:
            ``True`` if new entries should be blocked.

        """
        if self._session_start_capital <= ZERO:
            return False
        total_pnl = sum((r.pnl for r in self._results), start=ZERO)
        max_loss = self.config.max_drawdown_pct * self._session_start_capital
        return total_pnl < ZERO - max_loss

    def _kelly_position_pct(self, entry_price: Decimal, signal: CopySignal) -> Decimal:
        """Compute the Kelly-optimal position fraction for a binary market.

        For a binary outcome token bought at ``entry_price``, the win payoff
        is ``1 - entry_price`` per token and the loss is ``entry_price``.
        Apply fractional Kelly (``kelly_fraction``) for safety and clamp to
        ``max_position_pct`` as an upper bound.

        When the current total capital is below the high-water mark by
        more than ``drawdown_throttle_pct``, reduce the sized fraction by
        50 % to slow bleeding during drawdowns.

        When ``signal_strength_sizing`` is enabled, scale the sized
        fraction by the signal's ``strength_score`` so stronger signals
        receive proportionally larger allocations.

        Args:
            entry_price: Price of the favoured-side token (0 < p < 1).
            signal: The copy signal for proportional sizing.

        Returns:
            Fraction of capital to allocate (0 to ``max_position_pct``).

        """
        if entry_price <= ZERO or entry_price >= _ONE:
            return self.config.max_position_pct

        b = (_ONE - entry_price) / entry_price  # win/loss ratio
        p = self._effective_win_rate()
        q = _ONE - p
        kelly = (b * p - q) / b

        if kelly <= ZERO:
            return ZERO

        sized = kelly * self.config.kelly_fraction

        # HWM drawdown throttle: halve sizing when below HWM by threshold
        _throttle_factor = Decimal("0.5")
        if self._high_water_mark > ZERO:
            total_capital = self._total_capital()
            throttle_level = self._high_water_mark * (_ONE - self.config.drawdown_throttle_pct)
            if total_capital < throttle_level:
                sized = sized * _throttle_factor

        # Signal strength proportional sizing
        if self.config.signal_strength_sizing:
            sized = sized * signal.strength_score

        return min(sized, self.config.max_position_pct)

    def _check_exposure_limits(
        self,
        signal: CopySignal,
        proposed_cost: Decimal,
        capital: Decimal,
    ) -> bool:
        """Check net directional and per-asset exposure limits.

        Net directional exposure offsets same-asset opposite-side positions
        (e.g. BTC-Up and BTC-Down cancel out) rather than using gross cost.

        Args:
            signal: The copy signal for the proposed entry.
            proposed_cost: Cost basis of the proposed new position.
            capital: Current available capital for limit calculations.

        Returns:
            ``True`` if exposure is within limits, ``False`` to skip entry.

        """
        same_side_cost = ZERO
        opposite_side_cost = ZERO
        for pos in self._positions.values():
            if pos.state != PositionState.UNHEDGED:
                continue
            if pos.signal.asset != signal.asset:
                continue
            if pos.favoured_side == signal.favoured_side:
                same_side_cost += pos.leg1.cost_basis
            else:
                opposite_side_cost += pos.leg1.cost_basis
        net_exposure = same_side_cost - opposite_side_cost + proposed_cost
        max_unhedged = capital * self.config.max_unhedged_exposure_pct
        if net_exposure > max_unhedged:
            logger.info(
                "  SKIP %s: net unhedged exposure $%.2f (same=$%.2f opp=$%.2f + $%.2f)"
                " > max $%.2f (%.0f%%)",
                signal.condition_id[:12],
                net_exposure,
                same_side_cost,
                opposite_side_cost,
                proposed_cost,
                max_unhedged,
                self.config.max_unhedged_exposure_pct * 100,
            )
            return False

        total_cap = self._total_capital()
        asset_cost = sum(
            p.total_cost_basis
            for p in self._positions.values()
            if p.signal.asset == signal.asset and p.favoured_side == signal.favoured_side
        )
        max_asset = total_cap * self.config.max_asset_exposure_pct
        if asset_cost + proposed_cost > max_asset:
            logger.info(
                "  SKIP %s: asset %s %s exposure $%.2f + $%.2f > max $%.2f (%.0f%%)",
                signal.condition_id[:12],
                signal.asset,
                signal.favoured_side,
                asset_cost,
                proposed_cost,
                max_asset,
                self.config.max_asset_exposure_pct * 100,
            )
            return False

        return True

    async def _open_position(self, signal: CopySignal) -> None:
        """Open a directional leg 1 position copying the whale.

        Fetch current CLOB prices, validate the entry price is not too
        high, compute position size, and either simulate or place a real
        order.

        Args:
            signal: The copy signal to act on.

        """
        now = int(time.time())
        prices = await self._fetch_clob_prices(signal.condition_id)

        if prices is None:
            return

        favoured_price = prices.get(signal.favoured_side, ZERO)
        if favoured_price <= ZERO:
            logger.warning(
                "No price for %s side on %s", signal.favoured_side, signal.condition_id[:12]
            )
            return

        if favoured_price > self.config.max_entry_price:
            logger.info(
                "  SKIP %s: %s price %.4f > max_entry %.2f",
                signal.condition_id[:12],
                signal.favoured_side,
                favoured_price,
                self.config.max_entry_price,
            )
            return

        # Paper slippage: worsen entry price to approximate live execution
        if not self.live and self.config.paper_slippage_pct > ZERO:
            favoured_price = favoured_price * (_ONE + self.config.paper_slippage_pct)

        capital = self._get_capital()
        position_pct = self._kelly_position_pct(favoured_price, signal)
        spend = capital * position_pct
        qty = (spend / favoured_price).quantize(Decimal("0.01"))

        if qty < _MIN_TOKEN_QTY:
            logger.warning(
                "Skipping %s: quantity %.2f below minimum", signal.condition_id[:12], qty
            )
            return

        proposed_cost = favoured_price * qty

        if not self._check_exposure_limits(signal, proposed_cost, capital):
            return

        cost_basis = favoured_price * qty
        leg1 = SideLeg(
            side=signal.favoured_side,
            entry_price=favoured_price,
            quantity=qty,
            cost_basis=cost_basis,
        )
        hedge_side = "Down" if signal.favoured_side == "Up" else "Up"

        # Place live order or record paper fill
        if self.live:
            tokens_by_side = await self._fetch_clob_tokens(signal.condition_id)
            if tokens_by_side is None:
                return
            token = tokens_by_side.get(signal.favoured_side)
            leg1 = await self._place_leg_order(leg1, token)
            if leg1 is None:
                logger.warning("Leg 1 order failed for %s", signal.condition_id[:12])
                return

        pos = OpenPosition(
            signal=signal,
            state=PositionState.UNHEDGED,
            leg1=leg1,
            hedge_leg=None,
            hedge_side=hedge_side,
            entry_time=now,
            is_paper=not self.live,
        )
        self._positions[signal.condition_id] = pos

        mode = "LIVE" if self.live else "PAPER"
        logger.info(
            "%s OPEN %s leg1=%s price=%.4f qty=%.2f cost=$%.4f asset=%s bias=%.1f",
            mode,
            signal.condition_id[:12],
            signal.favoured_side,
            favoured_price,
            qty,
            cost_basis,
            signal.asset,
            signal.bias_ratio,
        )

    async def _build_price_cache(self) -> dict[str, dict[str, Decimal]]:
        """Fetch current CLOB prices once for all open positions.

        Build a mapping from condition_id to price dict so that
        ``_check_take_profits``, ``_check_defensive_hedges``, and
        ``_check_hedge_opportunities`` share a single set of price fetches
        per poll cycle instead of each fetching independently.

        Returns:
            Dict mapping condition_id to ``{"Up": price, "Down": price}``.

        """
        cache: dict[str, dict[str, Decimal]] = {}
        for cid in self._positions:
            prices = await self._fetch_clob_prices(cid)
            if prices is not None:
                cache[cid] = prices
        return cache

    async def _check_take_profits(
        self,
        price_cache: dict[str, dict[str, Decimal]],
    ) -> None:
        """Take profit via hedging (preferred) or selling when leg 1 rises.

        For each UNHEDGED position, check if the favoured side price has
        risen by ``take_profit_pct`` above entry. When triggered, prefer
        buying the opposite side to lock in a guaranteed settlement profit
        (combined < $1.00). Only fall back to selling if the hedge price
        is too expensive.

        Args:
            price_cache: Pre-fetched prices keyed by condition_id.

        """
        unhedged = [
            (cid, pos)
            for cid, pos in self._positions.items()
            if pos.state == PositionState.UNHEDGED
        ]

        for cid, pos in unhedged:
            prices = price_cache.get(cid)
            if prices is None:
                prices = await self._fetch_clob_prices(cid)
            if prices is None:
                continue

            current_price = prices.get(pos.leg1.side, ZERO)
            take_target = pos.leg1.entry_price * (_ONE + self.config.take_profit_pct)
            if current_price < take_target:
                continue

            # Prefer take-profit by hedging the opposite side
            hedge_price = prices.get(pos.hedge_side, ZERO)
            effective_leg1 = pos.leg1.cost_basis / pos.leg1.quantity
            combined = effective_leg1 + hedge_price

            if hedge_price > ZERO and combined < _ONE:
                hedged = await self._take_profit_hedge(cid, pos, hedge_price, combined)
                if hedged:
                    continue

            # Sell fallback: hedge too expensive or failed
            await self._take_profit_sell(cid, pos, current_price, take_target)

    async def _take_profit_hedge(
        self,
        cid: str,
        pos: OpenPosition,
        hedge_price: Decimal,
        combined: Decimal,
    ) -> bool:
        """Execute take-profit by hedging the opposite side.

        Buy the opposite side to lock in a guaranteed settlement profit.
        Return ``True`` if the hedge was placed successfully.

        Args:
            cid: Market condition_id.
            pos: The open position to hedge.
            hedge_price: Current price of the hedge side.
            combined: Sum of effective leg1 price and hedge price.

        Returns:
            ``True`` if hedged, ``False`` if hedge failed or was partial.

        """
        hedge_qty = pos.leg1.quantity
        if not self.live and self.config.paper_slippage_pct > ZERO:
            hedge_price = hedge_price * (_ONE + self.config.paper_slippage_pct)
        hedge_cost = hedge_price * hedge_qty
        hedge_leg = SideLeg(
            side=pos.hedge_side,
            entry_price=hedge_price,
            quantity=hedge_qty,
            cost_basis=hedge_cost,
        )

        if self.live:
            tokens_by_side = await self._fetch_clob_tokens(cid)
            if tokens_by_side is None:
                return False
            token = tokens_by_side.get(pos.hedge_side)
            hedge_leg = await self._place_leg_order(hedge_leg, token, is_hedge=True)
            if hedge_leg is None:
                return False
            if hedge_leg.quantity < pos.leg1.quantity:
                logger.warning(
                    "Partial take-profit hedge on %s: hedge=%.2f < leg1=%.2f",
                    cid[:12],
                    hedge_leg.quantity,
                    pos.leg1.quantity,
                )
                return False

        pos.hedge_leg = hedge_leg
        pos.state = PositionState.HEDGED
        self._consecutive_losses = 0

        mode = "LIVE" if self.live else "PAPER"
        logger.info(
            "%s TAKE-PROFIT-HEDGE %s hedge_side=%s hedge_price=%.4f combined=%.4f entry=%.4f",
            mode,
            cid[:12],
            pos.hedge_side,
            hedge_price,
            combined,
            pos.leg1.entry_price,
        )
        return True

    async def _take_profit_sell(
        self,
        cid: str,
        pos: OpenPosition,
        current_price: Decimal,
        take_target: Decimal,
    ) -> None:
        """Execute take-profit by selling leg 1 tokens.

        Fallback path when hedging is too expensive (combined >= $1.00).

        Args:
            cid: Market condition_id.
            pos: The open position to sell.
            current_price: Current favoured-side price.
            take_target: The take-profit trigger price.

        """
        if not self.live and self.config.paper_slippage_pct > ZERO:
            current_price = current_price * (_ONE - self.config.paper_slippage_pct)

        pnl = current_price * pos.leg1.quantity - pos.leg1.cost_basis

        if self.live:
            tokens_by_side = await self._fetch_clob_tokens(cid)
            if tokens_by_side is None:
                return
            token = tokens_by_side.get(pos.leg1.side)
            if token is None or self._executor is None:
                return
            response = await self._executor.place_order(
                token.token_id,
                "SELL",
                current_price,
                pos.leg1.quantity,
                order_type="market",
            )
            if response is None:
                return
            pnl = response.price * response.filled - pos.leg1.cost_basis
            pos.leg1.order_ids.append(response.order_id)

        pos.state = PositionState.EXITED
        self._positions.pop(cid)

        result = CopyResult(
            signal=pos.signal,
            state=PositionState.EXITED,
            leg1_side=pos.leg1.side,
            leg1_entry=pos.leg1.entry_price,
            leg1_qty=pos.leg1.quantity,
            hedge_entry=None,
            hedge_qty=None,
            total_cost_basis=pos.leg1.cost_basis,
            entry_time=pos.entry_time,
            exit_time=int(time.time()),
            winning_side=pos.leg1.side,
            pnl=pnl,
            is_paper=pos.is_paper,
            order_ids=tuple(pos.all_order_ids),
        )
        self._results.append(result)
        self._consecutive_losses = 0
        await self._persist_result(result)

        mode = "LIVE" if self.live else "PAPER"
        logger.info(
            "%s TAKE-PROFIT-SELL %s price=%.4f target=%.4f entry=%.4f pnl=%.4f cost=$%.4f",
            mode,
            cid[:12],
            current_price,
            take_target,
            pos.leg1.entry_price,
            pnl,
            pos.leg1.cost_basis,
        )

    async def _check_defensive_hedges(
        self,
        price_cache: dict[str, dict[str, Decimal]],
    ) -> None:
        """Buy the opposite side when leg 1 price drops, capping loss at settlement.

        For each UNHEDGED position, check if the favoured side price has
        fallen below ``entry_price * (1 - defensive_hedge_pct)``. If so,
        buy the opposite side with equal token quantity instead of selling
        into a thin book to cap maximum loss at settlement.

        Args:
            price_cache: Pre-fetched prices keyed by condition_id.

        """
        unhedged = [
            (cid, pos)
            for cid, pos in self._positions.items()
            if pos.state == PositionState.UNHEDGED
        ]

        for cid, pos in unhedged:
            prices = price_cache.get(cid)
            if prices is None:
                prices = await self._fetch_clob_prices(cid)
            if prices is None:
                continue

            current_price = prices.get(pos.leg1.side, ZERO)
            trigger_price = pos.leg1.entry_price * (_ONE - self.config.defensive_hedge_pct)
            if current_price >= trigger_price:
                continue

            # Defensive hedge — buy the opposite side to cap loss
            hedge_price = prices.get(pos.hedge_side, ZERO)
            if hedge_price <= ZERO:
                continue

            hedge_qty = pos.leg1.quantity
            if hedge_qty < _MIN_TOKEN_QTY:
                continue

            # Paper slippage on hedge price
            if not self.live and self.config.paper_slippage_pct > ZERO:
                hedge_price = hedge_price * (_ONE + self.config.paper_slippage_pct)

            hedge_cost = hedge_price * hedge_qty
            hedge_leg = SideLeg(
                side=pos.hedge_side,
                entry_price=hedge_price,
                quantity=hedge_qty,
                cost_basis=hedge_cost,
            )

            if self.live:
                tokens_by_side = await self._fetch_clob_tokens(cid)
                if tokens_by_side is None:
                    continue
                token = tokens_by_side.get(pos.hedge_side)
                hedge_leg = await self._place_leg_order(hedge_leg, token, is_hedge=True)
                if hedge_leg is None:
                    continue

                # Partial fill guard — stay UNHEDGED if hedge qty doesn't match leg 1
                if hedge_leg.quantity < pos.leg1.quantity:
                    logger.warning(
                        "Partial defensive hedge fill on %s: hedge=%.2f < leg1=%.2f,"
                        " staying UNHEDGED",
                        cid[:12],
                        hedge_leg.quantity,
                        pos.leg1.quantity,
                    )
                    continue

            pos.hedge_leg = hedge_leg
            pos.state = PositionState.HEDGED

            effective_leg1_price = pos.leg1.cost_basis / pos.leg1.quantity
            combined = effective_leg1_price + hedge_price
            max_loss_per_token = combined - _ONE if combined > _ONE else ZERO

            mode = "LIVE" if self.live else "PAPER"
            logger.info(
                "%s DEFENSIVE-HEDGE %s leg1_price=%.4f trigger=%.4f"
                " hedge_side=%s hedge_price=%.4f combined=%.4f max_loss_per_token=%.4f",
                mode,
                cid[:12],
                current_price,
                trigger_price,
                pos.hedge_side,
                hedge_price,
                combined,
                max_loss_per_token,
            )

    def _effective_hedge_spread(self, signal: CopySignal) -> Decimal:
        """Compute the effective max spread cost, applying urgency bump near expiry.

        Deduct per-leg CLOB fees from the configured max spread cost. If the
        remaining time fraction of the market window is below the urgency
        threshold, relax the spread by ``hedge_urgency_spread_bump``.

        Args:
            signal: The copy signal containing window timestamps.

        Returns:
            The effective maximum combined cost threshold.

        """
        fee_cost = 2 * self.config.clob_fee_rate
        effective = self.config.max_spread_cost - fee_cost

        window_duration = signal.window_end_ts - signal.window_start_ts
        if window_duration > 0:
            now = int(time.time())
            time_remaining = signal.window_end_ts - now
            time_fraction = Decimal(str(time_remaining)) / Decimal(str(window_duration))
            if time_fraction < self.config.hedge_urgency_threshold:
                effective = min(
                    effective + self.config.hedge_urgency_spread_bump,
                    Decimal("0.99"),
                )
        return effective

    async def _check_hedge_opportunities(
        self,
        price_cache: dict[str, dict[str, Decimal]],
    ) -> None:
        """Scan unhedged positions and place opportunistic hedge legs.

        For each UNHEDGED position, check whether the hedge side is cheap
        enough to lock in a guaranteed spread profit. Buy the same token
        quantity as leg 1 so that whichever side wins pays out
        ``qty * $1.00``, guaranteeing ``qty * (1.0 - combined)`` profit.

        Args:
            price_cache: Pre-fetched prices keyed by condition_id.

        """
        unhedged = [
            (cid, pos)
            for cid, pos in self._positions.items()
            if pos.state == PositionState.UNHEDGED
        ]

        for cid, pos in unhedged:
            prices = price_cache.get(cid)
            if prices is None:
                prices = await self._fetch_clob_prices(cid)
            if prices is None:
                continue

            hedge_price = prices.get(pos.hedge_side, ZERO)
            if hedge_price <= ZERO:
                continue

            effective_leg1_price = pos.leg1.cost_basis / pos.leg1.quantity
            effective_max_spread = self._effective_hedge_spread(pos.signal)

            combined = effective_leg1_price + hedge_price
            if combined > effective_max_spread:
                logger.debug(
                    "  HEDGE-WAIT %s: combined=%.4f > target=%.4f",
                    cid[:12],
                    combined,
                    effective_max_spread,
                )
                continue

            # Hedge opportunity found — match leg 1's token quantity so
            # that whichever side wins pays qty * $1.00, locking in
            # guaranteed profit of qty * (1 - combined).
            hedge_qty = pos.leg1.quantity

            if hedge_qty < _MIN_TOKEN_QTY:
                logger.debug("  HEDGE-SKIP %s: qty %.2f below minimum", cid[:12], hedge_qty)
                continue

            # Paper slippage on hedge price
            if not self.live and self.config.paper_slippage_pct > ZERO:
                hedge_price = hedge_price * (_ONE + self.config.paper_slippage_pct)

            hedge_cost = hedge_price * hedge_qty
            hedge_leg = SideLeg(
                side=pos.hedge_side,
                entry_price=hedge_price,
                quantity=hedge_qty,
                cost_basis=hedge_cost,
            )

            if self.live:
                tokens_by_side = await self._fetch_clob_tokens(cid)
                if tokens_by_side is None:
                    continue
                token = tokens_by_side.get(pos.hedge_side)
                hedge_leg = await self._place_leg_order(hedge_leg, token, is_hedge=True)
                if hedge_leg is None:
                    continue

                # Partial hedge fill guard — stay UNHEDGED if hedge quantity
                # doesn't match leg 1 (FOK should prevent this, but safety net)
                if hedge_leg.quantity < pos.leg1.quantity:
                    logger.warning(
                        "Partial hedge fill on %s: hedge=%.2f < leg1=%.2f, staying UNHEDGED",
                        cid[:12],
                        hedge_leg.quantity,
                        pos.leg1.quantity,
                    )
                    continue

            pos.hedge_leg = hedge_leg
            pos.state = PositionState.HEDGED

            spread_discount = _ONE - combined
            mode = "LIVE" if self.live else "PAPER"
            logger.info(
                "%s HEDGE %s side=%s price=%.4f qty=%.2f combined=%.4f spread_discount=%.4f",
                mode,
                cid[:12],
                pos.hedge_side,
                hedge_price,
                hedge_qty,
                combined,
                spread_discount,
            )

    async def _place_leg_order(
        self,
        leg: SideLeg | None,
        token: MarketToken | None,
        *,
        is_hedge: bool = False,
    ) -> SideLeg | None:
        """Place a live order for a single leg and adjust from the response.

        After placement, update the leg's quantity and cost basis to reflect
        the actual fill reported by the CLOB API.  If the order fills zero
        tokens (e.g. FOK rejected), return ``None``.

        Args:
            leg: The side leg to place, or ``None`` to skip.
            token: The CLOB token for this side.
            is_hedge: When ``True`` and ``config.hedge_with_market_orders``
                is enabled, force the order to use FOK market execution.

        Returns:
            The leg with fill-adjusted fields, or ``None`` if skipped/failed.

        """
        if leg is None or token is None or self._executor is None:
            return None

        order_type: str | None = None
        if is_hedge and self.config.hedge_with_market_orders:
            order_type = "market"

        response = await self._executor.place_order(
            token.token_id, "BUY", leg.entry_price, leg.quantity, order_type=order_type
        )
        if response is None:
            return None

        # Track actual fills from the order response (A)
        if response.filled == ZERO:
            logger.warning("Order %s filled 0 tokens — treating as failure", response.order_id)
            return None

        if response.filled < leg.quantity:
            logger.warning(
                "Partial fill on %s: requested=%.2f filled=%.2f",
                response.order_id,
                leg.quantity,
                response.filled,
            )

        leg.quantity = response.filled
        leg.cost_basis = response.price * response.filled
        leg.order_ids.append(response.order_id)
        return leg

    async def _fetch_clob_prices(self, condition_id: str) -> dict[str, Decimal] | None:
        """Fetch current CLOB token prices for both sides of a market.

        Use the authenticated Polymarket client to get live prices.
        Both paper and live mode use the same price source for accuracy.

        Args:
            condition_id: Polymarket market condition identifier.

        Returns:
            Dict mapping ``"Up"`` and ``"Down"`` to prices, or ``None`` on error.

        """
        if self.client is None:
            logger.warning("No client available for price fetch")
            return None

        try:
            market = await self.client.get_market(condition_id)
        except Exception:
            logger.exception("Failed to fetch market %s", condition_id[:12])
            return None

        return _prices_from_tokens({t.outcome: t for t in market.tokens})

    async def _fetch_clob_tokens(self, condition_id: str) -> dict[str, MarketToken] | None:
        """Fetch CLOB tokens keyed by outcome side.

        Args:
            condition_id: Polymarket market condition identifier.

        Returns:
            Dict mapping outcome name to ``MarketToken``, or ``None`` on error.

        """
        if self.client is None:
            return None

        try:
            market = await self.client.get_market(condition_id)
        except Exception:
            logger.exception("Failed to fetch market tokens %s", condition_id[:12])
            return None

        return {t.outcome: t for t in market.tokens}

    async def _close_expired_positions(self) -> None:
        """Close positions whose market windows have expired.

        Fetch actual spot price from Binance 1-min candles to determine
        the market outcome. P&L depends on position state:
        - HEDGED: guaranteed profit (winning_qty - total_cost).
        - UNHEDGED: directional P&L (leg1 tokens worth $1 or $0).
        """
        now = int(time.time())
        expired = [cid for cid, pos in self._positions.items() if pos.signal.window_end_ts <= now]

        for cid in expired:
            pos = self._positions.pop(cid)
            winning_side = await self._resolve_outcome(pos)
            outcome_known = winning_side is not None

            if outcome_known:
                pnl = compute_pnl(pos, winning_side)
            elif pos.state == PositionState.HEDGED:
                # Unknown outcome but hedged → can't determine which leg won
                pnl = ZERO
            else:
                # Unknown outcome and unhedged → conservative: assume total loss
                pnl = ZERO - pos.leg1.cost_basis

            closed = CopyResult(
                signal=pos.signal,
                state=pos.state,
                leg1_side=pos.leg1.side,
                leg1_entry=pos.leg1.entry_price,
                leg1_qty=pos.leg1.quantity,
                hedge_entry=pos.hedge_leg.entry_price if pos.hedge_leg else None,
                hedge_qty=pos.hedge_leg.quantity if pos.hedge_leg else None,
                total_cost_basis=pos.total_cost_basis,
                entry_time=pos.entry_time,
                exit_time=now,
                winning_side=winning_side,
                pnl=pnl,
                is_paper=pos.is_paper,
                order_ids=tuple(pos.all_order_ids),
                outcome_known=outcome_known,
            )
            self._results.append(closed)
            await self._persist_result(closed)

            # Update high-water mark after profitable close
            if pnl > ZERO:
                current_total = self._total_capital()
                self._high_water_mark = max(self._high_water_mark, current_total)

            # Circuit breaker tracking for unhedged positions with known outcomes
            if pos.state == PositionState.UNHEDGED and outcome_known:
                if pnl > ZERO:
                    self._consecutive_losses = 0
                else:
                    self._record_loss()

            outcome_label = "WIN" if pnl > ZERO else "LOSS"
            if not outcome_known:
                outcome_label = "UNKNOWN"
            logger.info(
                "%s CLOSE %s %s pnl=%.4f cost=$%.4f winning=%s leg1=%s state=%s asset=%s",
                "PAPER" if pos.is_paper else "LIVE",
                cid[:12],
                outcome_label,
                pnl,
                pos.total_cost_basis,
                winning_side,
                pos.leg1.side,
                pos.state.value,
                pos.signal.asset,
            )

    async def _resolve_outcome(self, pos: OpenPosition) -> str | None:
        """Determine which side won based on Binance spot price movement.

        Fetch Binance 1-min candles for the market window and compare
        the opening price against the closing price. Return ``None``
        when candle data is unavailable so the caller can apply
        conservative fallback P&L.

        Args:
            pos: The open position with signal and window timestamps.

        Returns:
            ``"Up"`` if price went up, ``"Down"`` if it went down, or
            ``None`` if candle data was unavailable.

        """
        assert self._binance is not None  # noqa: S101
        signal = pos.signal

        try:
            provider = BinanceCandleProvider(self._binance)
            candles = await provider.get_candles(
                signal.asset,
                Interval.M1,
                signal.window_start_ts,
                signal.window_end_ts,
            )
        except Exception:
            logger.warning("Failed to fetch candles for %s, outcome unknown", signal.asset)
            return None

        if not candles:
            logger.warning("No candles for %s window, outcome unknown", signal.asset)
            return None

        open_price = candles[0].open
        close_price = candles[-1].close
        price_went_up = close_price > open_price
        winning_side = "Up" if price_went_up else "Down"

        logger.info(
            "  SPOT %s open=%.2f close=%.2f direction=%s leg1=%s",
            signal.asset,
            open_price,
            close_price,
            winning_side,
            pos.leg1.side,
        )

        return winning_side

    def _log_heartbeat(self) -> None:
        """Emit a heartbeat via the shared HeartbeatLogger."""
        assert self._detector is not None  # noqa: S101
        total_pnl = sum(r.pnl for r in self._results)
        stopped = sum(1 for r in self._results if r.state == PositionState.STOPPED)
        exited = sum(1 for r in self._results if r.state == PositionState.EXITED)
        self._heartbeat.maybe_log(
            polls=self._poll_count,
            window_trades=self._detector.window_size,
            unhedged=sum(1 for p in self._positions.values() if p.state == PositionState.UNHEDGED),
            hedged=sum(1 for p in self._positions.values() if p.state == PositionState.HEDGED),
            closed=len(self._results),
            stopped=stopped,
            exited=exited,
            pnl=float(total_pnl),
        )

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

    async def _persist_result(self, result: CopyResult) -> None:
        """Persist a closed trade result to the database if a repo is attached.

        Convert the in-memory ``CopyResult`` to a ``CopyResultRecord`` and
        save it immediately. Log a warning on failure but do not re-raise
        so the trading loop continues.

        Args:
            result: The closed trade result to persist.

        """
        if self._repo is None:
            return
        try:
            record = CopyResultRecord.from_copy_result(result)
            await self._repo.save_result(record)
        except Exception:
            logger.exception(
                "Failed to persist copy result for %s", result.signal.condition_id[:12]
            )

    def _record_loss(self) -> None:
        """Increment consecutive losses and activate circuit breaker if needed.

        When the configured loss threshold is reached, pause new entries
        for ``circuit_breaker_cooldown`` seconds. A threshold of ``0``
        disables the breaker entirely.
        """
        self._consecutive_losses += 1
        threshold = self.config.circuit_breaker_losses
        if threshold > 0 and self._consecutive_losses >= threshold:
            self._circuit_breaker_until = int(time.time()) + self.config.circuit_breaker_cooldown
            logger.warning(
                "CIRCUIT-BREAKER triggered after %d consecutive losses, pausing for %ds",
                self._consecutive_losses,
                self.config.circuit_breaker_cooldown,
            )

    @property
    def positions(self) -> dict[str, OpenPosition]:
        """Return the current open positions (read-only copy)."""
        return dict(self._positions)

    @property
    def results(self) -> list[CopyResult]:
        """Return all closed trade results (read-only copy)."""
        return list(self._results)

    @property
    def poll_count(self) -> int:
        """Return the number of completed poll cycles."""
        return self._poll_count


def compute_pnl(pos: OpenPosition, winning_side: str) -> Decimal:
    """Compute P&L for a position at resolution.

    For HEDGED positions, the winning leg pays $1.00/token. P&L depends
    on which leg won:
    - If leg1 wins: leg1.quantity * $1.00 - total_cost
    - If hedge wins: hedge.quantity * $1.00 - total_cost

    For UNHEDGED positions (directional only):
    - If leg1 wins: leg1.quantity * $1.00 - leg1.cost_basis
    - If leg1 loses: $0 - leg1.cost_basis

    STOPPED and EXITED positions have P&L pre-computed at exit time and
    should not be passed to this function (they are closed inline).

    Args:
        pos: The open position to evaluate.
        winning_side: Which side won (``"Up"`` or ``"Down"``).

    Returns:
        Realised P&L in USDC.

    """
    if pos.state == PositionState.HEDGED and pos.hedge_leg is not None:
        winning_qty = pos.leg1.quantity if winning_side == pos.leg1.side else pos.hedge_leg.quantity
        return winning_qty - pos.total_cost_basis

    # UNHEDGED: purely directional
    if winning_side == pos.leg1.side:
        return pos.leg1.quantity - pos.leg1.cost_basis
    return ZERO - pos.leg1.cost_basis


def _prices_from_tokens(tokens_by_side: dict[str, MarketToken]) -> dict[str, Decimal]:
    """Extract prices from CLOB tokens, deriving missing sides.

    Args:
        tokens_by_side: Market tokens keyed by outcome name.

    Returns:
        Dict mapping ``"Up"`` and ``"Down"`` to their Decimal prices.

    """
    prices: dict[str, Decimal] = {}
    for side in ("Up", "Down"):
        token = tokens_by_side.get(side)
        if token and token.price > ZERO:
            prices[side] = token.price

    if "Up" in prices and "Down" not in prices:
        prices["Down"] = _ONE - prices["Up"]
    elif "Down" in prices and "Up" not in prices:
        prices["Up"] = _ONE - prices["Down"]

    return prices
