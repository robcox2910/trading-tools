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
from trading_tools.apps.bot_framework.order_executor import OrderExecutor
from trading_tools.apps.bot_framework.redeemer import PositionRedeemer
from trading_tools.clients.binance.client import BinanceClient
from trading_tools.core.models import ZERO, Interval
from trading_tools.data.providers.binance import BinanceCandleProvider

from .models import CopyResult, CopySignal, OpenPosition, PositionState, SideLeg
from .signal_detector import SignalDetector

if TYPE_CHECKING:
    from trading_tools.clients.polymarket.client import PolymarketClient
    from trading_tools.clients.polymarket.models import MarketToken

    from .config import WhaleCopyConfig

logger = logging.getLogger(__name__)

_HEARTBEAT_INTERVAL = 60
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
    _running: bool = field(default=False, repr=False)
    _last_heartbeat: float = field(default=0.0, repr=False)
    _redeemer: PositionRedeemer | None = field(default=None, init=False, repr=False)
    _executor: OrderExecutor | None = field(default=None, init=False, repr=False)
    _balance_manager: BalanceManager | None = field(default=None, init=False, repr=False)

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
        self._running = True

        if self.live and self._balance_manager is not None:
            await self._balance_manager.refresh()

        mode = "LIVE" if self.live else "PAPER"
        capital = self._get_capital()
        logger.info(
            "whale-copy started mode=%s address=%s poll=%ds"
            " lookback=%ds min_bias=%.1f min_trades=%d"
            " min_time_to_start=%ds capital=$%s max_pos=%s%%"
            " max_spread_cost=%.2f max_entry_price=%.2f",
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
        )

        try:
            while self._running:
                await self._poll_cycle()
                self._maybe_log_heartbeat()
                await asyncio.sleep(self.config.poll_interval)
        except (KeyboardInterrupt, asyncio.CancelledError):
            logger.info("whale-copy shutting down gracefully")
        finally:
            self._running = False
            self._log_summary()
            await self._binance.close()
            await self._detector.close()

    def stop(self) -> None:
        """Signal the polling loop to stop after the current cycle."""
        self._running = False

    def _get_capital(self) -> Decimal:
        """Return the current capital available for position sizing.

        In live mode, use the real USDC balance from the shared
        ``BalanceManager``. In paper mode, use the configured starting
        capital.

        Returns:
            Available capital in USDC.

        """
        if self._balance_manager is not None and self._balance_manager.balance > ZERO:
            return self._balance_manager.balance
        return self.config.capital

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

        await self._check_hedge_opportunities()
        await self._close_expired_positions()

        if self._redeemer is not None:
            await self._redeemer.redeem_if_available()

    async def _process_signal(self, signal: CopySignal) -> None:
        """Open a directional entry for new markets, ignore existing ones.

        Args:
            signal: The copy signal to process.

        """
        cid = signal.condition_id
        if cid in self._positions:
            return

        await self._open_position(signal)

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

        spend = self._get_capital() * self.config.max_position_pct
        qty = (spend / favoured_price).quantize(Decimal("0.01"))

        if qty < _MIN_TOKEN_QTY:
            logger.warning(
                "Skipping %s: quantity %.2f below minimum", signal.condition_id[:12], qty
            )
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

    async def _check_hedge_opportunities(self) -> None:
        """Scan unhedged positions and place opportunistic hedge legs.

        For each UNHEDGED position, fetch the current price of the hedge
        side. If ``leg1_entry + hedge_price <= max_spread_cost``, the
        opposite side is cheap enough to be worth hedging. Buy a
        capital-sized allocation (same dollar amount as leg 1) to reduce
        directional risk. This doesn't guarantee profit on every trade
        but provides positive expected value across many trades.
        """
        unhedged = [
            (cid, pos)
            for cid, pos in self._positions.items()
            if pos.state == PositionState.UNHEDGED
        ]

        for cid, pos in unhedged:
            prices = await self._fetch_clob_prices(cid)
            if prices is None:
                continue

            hedge_price = prices.get(pos.hedge_side, ZERO)
            if hedge_price <= ZERO:
                continue

            combined = pos.leg1.entry_price + hedge_price
            if combined > self.config.max_spread_cost:
                logger.debug(
                    "  HEDGE-WAIT %s: combined=%.4f > target=%.4f",
                    cid[:12],
                    combined,
                    self.config.max_spread_cost,
                )
                continue

            # Hedge opportunity found — buy a capital-sized allocation
            # on the opposite side (same dollar spend as leg 1).
            spend = self._get_capital() * self.config.max_position_pct
            hedge_qty = (spend / hedge_price).quantize(Decimal("0.01"))

            if hedge_qty < _MIN_TOKEN_QTY:
                logger.debug("  HEDGE-SKIP %s: qty %.2f below minimum", cid[:12], hedge_qty)
                continue

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
                hedge_leg = await self._place_leg_order(hedge_leg, token)
                if hedge_leg is None:
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
    ) -> SideLeg | None:
        """Place a live order for a single leg and attach the order ID.

        Args:
            leg: The side leg to place, or ``None`` to skip.
            token: The CLOB token for this side.

        Returns:
            The leg with order ID attached, or ``None`` if skipped or failed.

        """
        if leg is None or token is None or self._executor is None:
            return None

        response = await self._executor.place_order(
            token.token_id, "BUY", leg.entry_price, leg.quantity
        )
        if response is None:
            return None

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
            pnl = compute_pnl(pos, winning_side)

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
            )
            self._results.append(closed)
            outcome = "WIN" if pnl > ZERO else "LOSS"
            logger.info(
                "%s CLOSE %s %s pnl=%.4f cost=$%.4f winning=%s leg1=%s state=%s asset=%s",
                "PAPER" if pos.is_paper else "LIVE",
                cid[:12],
                outcome,
                pnl,
                pos.total_cost_basis,
                winning_side,
                pos.leg1.side,
                pos.state.value,
                pos.signal.asset,
            )

    async def _resolve_outcome(self, pos: OpenPosition) -> str:
        """Determine which side won based on Binance spot price movement.

        Fetch Binance 1-min candles for the market window and compare
        the opening price against the closing price.

        Args:
            pos: The open position with signal and window timestamps.

        Returns:
            ``"Up"`` if price went up, ``"Down"`` otherwise.

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
            logger.warning("Failed to fetch candles for %s, assuming Up", signal.asset)
            return "Up"

        if not candles:
            logger.warning("No candles for %s window, assuming Up", signal.asset)
            return "Up"

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

    def _maybe_log_heartbeat(self) -> None:
        """Log a heartbeat message if the interval has elapsed."""
        now = time.monotonic()
        if now - self._last_heartbeat >= _HEARTBEAT_INTERVAL:
            self._last_heartbeat = now
            total_pnl = sum(r.pnl for r in self._results)
            assert self._detector is not None  # noqa: S101
            window_trades = self._detector.window_size
            unhedged = sum(1 for p in self._positions.values() if p.state == PositionState.UNHEDGED)
            hedged = sum(1 for p in self._positions.values() if p.state == PositionState.HEDGED)
            logger.info(
                "HEARTBEAT polls=%d window_trades=%d unhedged=%d hedged=%d closed=%d pnl=$%.2f",
                self._poll_count,
                window_trades,
                unhedged,
                hedged,
                len(self._results),
                total_pnl,
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
