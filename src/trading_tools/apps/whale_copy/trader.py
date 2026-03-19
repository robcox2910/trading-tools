"""Whale copy trading engine for Polymarket Up/Down markets.

Continuously mirror the net directional positioning of tracked whale
addresses.  Each poll cycle re-reads the whale's current direction and
buys tokens on whichever side they favour RIGHT NOW.  The whale_side
on each position updates dynamically — it is never locked in.

Guaranteed profit when the whale is correct: winning tokens pay $1.00,
losing tokens (if whale flipped mid-window) pay $0.00.
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
from trading_tools.apps.spread_capture.market_scanner import MarketScanner
from trading_tools.apps.spread_capture.models import (
    PositionState,
    SideLeg,
    SpreadResult,
    SpreadResultRecord,
)
from trading_tools.apps.whale_copy.models import WhalePosition
from trading_tools.clients.binance.client import BinanceClient
from trading_tools.clients.binance.exceptions import BinanceError
from trading_tools.core.models import HUNDRED, ONE, ZERO, Interval
from trading_tools.data.providers.binance import BinanceCandleProvider

if TYPE_CHECKING:
    from trading_tools.apps.spread_capture.repository import SpreadResultRepository
    from trading_tools.apps.whale_copy.signal import WhaleSignalClient
    from trading_tools.clients.polymarket.client import PolymarketClient

    from .config import WhaleCopyConfig

logger = logging.getLogger(__name__)

_BALANCE_REFRESH_POLLS = 60
_WIN_PRICE = Decimal("1.0")
_MIN_TOKEN_QTY = Decimal(5)
_SUMMARY_INTERVAL = 900  # 15 minutes
_REACTIVE_SLEEP = 0.5  # seconds between iterations (WS triggers fills)


def _empty_position_dict() -> dict[str, WhalePosition]:
    """Return an empty dict for dataclass default_factory."""
    return {}


def _empty_result_list() -> list[SpreadResult]:
    """Return an empty list for dataclass default_factory."""
    return []


@dataclass
class WhaleCopyTrader:
    """Whale copy trading engine for Polymarket Up/Down markets.

    Poll whale activity via the Polymarket Data API, accumulate tokens
    on the whale's favoured side, and settle at market expiry.  The
    whale direction is re-evaluated every poll — positions dynamically
    track the whale's current consensus rather than locking in a signal.

    Attributes:
        config: Immutable service configuration.
        signal_client: Client for querying whale directional signals.
        live: Enable live trading (requires ``client``).
        client: Authenticated Polymarket client for CLOB data and orders.

    """

    config: WhaleCopyConfig
    signal_client: WhaleSignalClient
    live: bool = False
    client: PolymarketClient | None = None
    _scanner: MarketScanner | None = field(default=None, repr=False)
    _binance: BinanceClient | None = field(default=None, repr=False)
    _positions: dict[str, WhalePosition] = field(default_factory=_empty_position_dict, repr=False)
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
        """Run the reactive loop until interrupted.

        Initialize the market scanner, start the WebSocket listener for
        real-time trade events, and enter a tight async loop.  Fills are
        triggered reactively when the WebSocket signals new trade
        activity, with a short sleep (0.5s) to avoid busy-waiting.
        Market discovery and settlement run on every iteration.
        """
        if self.client is None:
            msg = "PolymarketClient is required — pass client at construction"
            raise RuntimeError(msg)

        self._scanner = MarketScanner(
            client=self.client,
            series_slugs=self.config.series_slugs,
            max_combined_cost=Decimal("1.00"),
            min_spread_margin=Decimal("-1.00"),
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
            "whale-copy started mode=%s capital=$%s"
            " max_pos=%s%% fill_size=%s"
            " max_open=%d slugs=%s ws=enabled",
            mode,
            capital,
            self.config.max_position_pct * Decimal(100),
            self.config.fill_size_tokens,
            self.config.max_open_positions,
            ",".join(self.config.series_slugs),
        )

        # Start WebSocket for real-time trade triggers
        initial_assets = await self._discover_asset_ids()
        await self.signal_client.start_ws(initial_assets)

        try:
            while not self._shutdown.should_stop:
                await self._poll_cycle()
                self._log_heartbeat()
                now = time.monotonic()
                if now >= self._summary_due:
                    self._log_periodic_summary()
                    self._summary_due = now + _SUMMARY_INTERVAL
                # Short sleep — WS triggers make us reactive, not polling
                await asyncio.sleep(_REACTIVE_SLEEP)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            self._log_summary()
            if self._binance is not None:  # pyright: ignore[reportUnnecessaryComparison]
                await self._binance.close()

    async def _discover_asset_ids(self) -> list[str]:
        """Discover all active market token IDs for WebSocket subscription.

        Scan for markets and collect all Up and Down token IDs so the
        WebSocket can subscribe to trade events on every tracked market.

        Returns:
            List of CLOB token identifiers for all active markets.

        """
        if self._scanner is None:
            return []
        opportunities = await self._scanner.scan_per_side(set(), Decimal("1.00"))
        asset_ids: list[str] = []
        for opp in opportunities:
            asset_ids.append(opp.up_token_id)
            asset_ids.append(opp.down_token_id)
        logger.info("Discovered %d asset IDs for WebSocket subscription", len(asset_ids))
        return asset_ids

    def set_repo(self, repo: SpreadResultRepository) -> None:
        """Attach a database repository for persisting settled trade results.

        Args:
            repo: An initialised ``SpreadResultRepository`` instance.

        """
        self._repo = repo

    def stop(self) -> None:
        """Signal the polling loop to stop after the current cycle."""
        self._shutdown.request()

    async def _poll_cycle(self) -> None:
        """Execute one discover-signal-fill-settle cycle.

        Settle expired positions, discover new markets, open positions
        for new markets, query whale signals and fill on each open
        position, and periodically refresh balance.
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

        # 1. Settle expired positions
        await self._settle_expired_positions()

        # 2. Discover new markets
        open_cids = set(self._positions.keys())
        opportunities = await self._scanner.scan_per_side(open_cids, Decimal("1.00"))

        # 3. Open positions for new markets
        now = int(time.time())
        positions_opened = False
        for opp in opportunities:
            if len(self._positions) >= self.config.max_open_positions:
                break
            if self._check_drawdown_halt():
                break
            if self._circuit_breaker_until > now:
                break

            budget = self._get_capital() * self.config.max_position_pct
            if budget < self.config.fill_size_tokens * self.config.max_price:
                continue

            pos = WhalePosition(
                opportunity=opp,
                state=PositionState.ACCUMULATING,
                up_leg=SideLeg("Up", ZERO, ZERO, ZERO),
                down_leg=SideLeg("Down", ZERO, ZERO, ZERO),
                entry_time=now,
                is_paper=not self.live,
                budget=budget,
            )
            self._positions[opp.condition_id] = pos
            positions_opened = True
            mode = "LIVE" if self.live else "PAPER"
            logger.info(
                "%s OPEN %s budget=$%.2f asset=%s",
                mode,
                opp.condition_id[:12],
                budget,
                opp.asset,
            )

        # 3b. Update WS subscription if new markets were opened
        if positions_opened:
            all_assets: list[str] = []
            for pos in self._positions.values():
                all_assets.append(pos.opportunity.up_token_id)
                all_assets.append(pos.opportunity.down_token_id)
            await self.signal_client.update_subscription(all_assets)

        # 4. Fill on each open position based on whale signal
        await self._fill_positions()

        # 5. Redeem resolved positions
        if self._redeemer is not None:
            await self._redeemer.redeem_if_available()

    async def _fill_positions(self) -> None:
        """Mirror whale volume split on each open position.

        For each ACCUMULATING position, query the whale's dollar volume
        on both sides and fill proportionally.  If the whale has $70 on
        Up and $30 on Down, allocate fills at 70/30.  This mirrors the
        whale's buy-then-hedge pattern instead of picking a single side.
        """
        now = int(time.time())

        for cid, pos in list(self._positions.items()):
            if pos.state != PositionState.ACCUMULATING:
                continue

            opp = pos.opportunity
            window_duration = opp.window_end_ts - opp.window_start_ts
            if window_duration <= 0:
                continue

            elapsed_pct = Decimal(str(now - opp.window_start_ts)) / Decimal(str(window_duration))
            if elapsed_pct > self.config.max_fill_age_pct:
                continue

            # Wait for the whale to show their real hand
            if elapsed_pct < self.config.min_fill_age_pct:
                continue

            if pos.total_cost_basis >= pos.budget:
                continue

            # Get whale volumes on BOTH sides
            up_vol, down_vol = await self.signal_client.get_volumes(
                opp.condition_id, window_start_ts=opp.window_start_ts
            )
            total_vol = up_vol + down_vol

            if total_vol == ZERO:
                logger.debug("No whale activity on %s", cid[:12])
                continue

            # Ignore tiny feint trades — wait for meaningful volume
            if total_vol < self.config.min_whale_volume:
                logger.debug(
                    "Low whale volume on %s: $%.2f < $%.2f",
                    cid[:12],
                    total_vol,
                    self.config.min_whale_volume,
                )
                continue

            # Track favoured side for logging
            favoured = "Up" if up_vol >= down_vol else "Down"
            if pos.whale_side is not None and pos.whale_side != favoured:
                logger.info(
                    "WHALE-FLIP %s old=%s new=%s up=$%.2f down=$%.2f",
                    cid[:12],
                    pos.whale_side,
                    favoured,
                    up_vol,
                    down_vol,
                )
            pos.whale_side = favoured

            # Fill both sides proportionally to whale volume split
            up_pct = up_vol / total_vol
            down_pct = down_vol / total_vol

            logger.debug(
                "WHALE-VOLUME %s up=$%.2f (%.0f%%) down=$%.2f (%.0f%%)",
                cid[:12],
                up_vol,
                up_pct * HUNDRED,
                down_vol,
                down_pct * HUNDRED,
            )

            if up_pct > ZERO:
                await self._execute_fill(pos, pos.up_leg, opp.up_token_id, cid, up_pct)
            if down_pct > ZERO:
                await self._execute_fill(pos, pos.down_leg, opp.down_token_id, cid, down_pct)

    async def _execute_fill(
        self,
        pos: WhalePosition,
        leg: SideLeg,
        token_id: str,
        cid: str,
        whale_pct: Decimal = ONE,
    ) -> None:
        """Execute a single fill on the given leg, sized by whale ratio.

        Fetch the order book and fill proportionally to the whale's
        volume split.  No max_price filter — we buy at whatever price
        the whale is buying to match their ratio.

        Args:
            pos: The whale copy position being filled.
            leg: The side leg to add the fill to.
            token_id: CLOB token ID for the side.
            cid: Condition ID (for logging).
            whale_pct: Fraction of whale volume on this side (0-1).
                Fill size is scaled by this ratio.

        """
        if self.client is None:
            return

        try:
            book = await self.client.get_order_book(token_id)
        except (httpx.HTTPError, Exception):
            logger.debug("Failed to fetch book for %s %s", cid[:12], leg.side)
            return

        if not book.asks:
            return

        ask_price = book.asks[0].price

        # Scale fill size by whale's ratio on this side
        base_qty = self.config.fill_size_tokens * whale_pct
        fill_qty = max(base_qty.quantize(Decimal(1)), _MIN_TOKEN_QTY)

        # Check depth constraint
        ask_depth = sum((level.size for level in book.asks), start=ZERO)
        max_qty = ask_depth * self.config.max_book_pct
        fill_qty = min(fill_qty, max_qty)
        if fill_qty < _MIN_TOKEN_QTY:
            logger.debug("Skip fill %s %s: depth too thin", cid[:12], leg.side)
            return

        # Check budget constraint
        remaining_budget = pos.budget - pos.total_cost_basis
        fill_cost = ask_price * fill_qty
        if fill_cost > remaining_budget:
            fill_qty = (remaining_budget / ask_price).quantize(Decimal("0.01"))
            if fill_qty < _MIN_TOKEN_QTY:
                return

        if self.live and self._executor is not None:
            resp = await self._executor.place_order(token_id, "BUY", ask_price, fill_qty)
            if resp is not None and resp.filled > ZERO:
                leg.add_fill(resp.price, resp.filled)
                leg.order_ids.append(resp.order_id)
                logger.info(
                    "LIVE FILL %s %s qty=%.2f price=%.4f cost=$%.4f",
                    cid[:12],
                    leg.side,
                    resp.filled,
                    resp.price,
                    resp.price * resp.filled,
                )
        else:
            # Paper fill with slippage
            fill_price = ask_price * (ONE + self.config.paper_slippage_pct)
            leg.add_fill(fill_price, fill_qty)
            mode = "PAPER"
            logger.info(
                "%s FILL %s %s qty=%.2f price=%.4f cost=$%.4f whale=%s",
                mode,
                cid[:12],
                leg.side,
                fill_qty,
                fill_price,
                fill_price * fill_qty,
                pos.whale_side,
            )

    async def _settle_expired_positions(self) -> None:
        """Settle positions whose market windows have expired.

        Resolve the outcome via Binance candles (or Polymarket in live
        mode), compute P&L, record results, and update risk controls.
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

            # Build return string for logging
            return_pct = (
                (pnl / pos.total_cost_basis * Decimal(100)) if pos.total_cost_basis > ZERO else ZERO
            )
            logger.info(
                "%s CLOSE %s %s pnl=$%.4f return=%.1f%% cost=$%.4f"
                " up_qty=%.2f down_qty=%.2f whale=%s winning=%s asset=%s",
                mode,
                cid[:12],
                outcome_label,
                pnl,
                return_pct,
                pos.total_cost_basis,
                pos.up_leg.quantity,
                pos.down_leg.quantity,
                pos.whale_side,
                winning_side,
                pos.opportunity.asset,
            )

    def _compute_pnl(self, pos: WhalePosition, winning_side: str | None) -> Decimal:
        """Compute P&L for a whale copy position at settlement.

        Both legs can have tokens if the whale flipped direction.
        Winning tokens pay $1.00 per token, losing tokens pay $0.00.
        Entry fees are deducted from the payout.

        Args:
            pos: The position being settled.
            winning_side: ``"Up"`` or ``"Down"``, or ``None``.

        Returns:
            Realised P&L in USDC (net of fees).

        """
        pnl = ZERO
        for leg in (pos.up_leg, pos.down_leg):
            if leg.quantity <= ZERO:
                continue
            fee = (
                compute_poly_fee(leg.entry_price, self.config.fee_rate, self.config.fee_exponent)
                * leg.quantity
            )
            if winning_side is not None and leg.side == winning_side:
                pnl += leg.quantity * _WIN_PRICE - leg.cost_basis - fee
            else:
                pnl += ZERO - leg.cost_basis - fee
        return pnl

    async def _resolve_outcome_live(self, pos: WhalePosition) -> str | None:
        """Resolve outcome using Polymarket's resolution status first.

        Fall back to Binance candle-based resolution if no match is found.

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

    async def _resolve_outcome(self, pos: WhalePosition) -> str | None:
        """Determine which side won via Binance spot price movement.

        Fetch Binance 1-min candles for the market window and compare
        opening vs closing price.

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
            winning_side = None

        logger.info(
            "  SPOT %s open=%.2f close=%.2f direction=%s",
            opp.asset,
            open_price,
            close_price,
            winning_side,
        )
        return winning_side

    def _get_capital(self) -> Decimal:
        """Return the current capital available for position sizing.

        In live mode, use the real USDC balance.  In paper mode, start
        from base capital, optionally add realised P&L, and subtract
        committed capital.

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

    @property
    def _committed_capital(self) -> Decimal:
        """Return the total cost basis of all open positions."""
        return sum(
            (pos.total_cost_basis for pos in self._positions.values()),
            start=ZERO,
        )

    def _check_drawdown_halt(self) -> bool:
        """Return ``True`` when session drawdown exceeds the configured limit."""
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
        for pos_cid, pos in self._positions.items():
            logger.info(
                "SUMMARY |   %s %s whale=%s up_qty=%.2f down_qty=%.2f cost=$%.2f",
                pos_cid[:12],
                pos.opportunity.asset,
                pos.whale_side,
                pos.up_leg.quantity,
                pos.down_leg.quantity,
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
                "Failed to persist result for %s",
                result.opportunity.condition_id[:12],
            )

    @property
    def positions(self) -> dict[str, WhalePosition]:
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
