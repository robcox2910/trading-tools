"""Core copy-trading engine that mirrors whale bets in real-time.

Run a tight polling loop that detects whale directional bias signals and
either simulates (paper) or executes (live) dual-side spread trades on
Polymarket.

Dual-side spread capture:
- Buy BOTH Up and Down tokens, splitting capital by the whale's volume
  allocation. The favoured side gets more capital (directional tilt), the
  unfavoured side provides hedging and spread capture.
- At resolution the winning side pays $1.00/token, the losing side $0.00.
  P&L = winning_leg_quantity - total_cost_basis.

Dynamic position management:
- OPEN: first time a market signals above threshold.
- TOP-UP: whale increases conviction (bias rises by ``topup_bias_delta``).
- FLIP: whale reverses direction — close existing, open opposite.
- CLOSE: market window expires — resolve P&L via Binance spot prices.

Performance design:
- 5-second default poll interval for minimal latency on 5-minute markets.
- Incremental signal detection (see ``SignalDetector``).
- Pre-authenticated client: Polymarket connection established at startup.
- GTC limit orders by default: matches whale's order style for better fills.
- Async throughout: no blocking calls in the hot path.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING

from trading_tools.clients.binance.client import BinanceClient
from trading_tools.clients.polymarket._gamma_client import GammaClient
from trading_tools.clients.polymarket.models import OrderRequest
from trading_tools.core.models import ZERO, Interval
from trading_tools.data.providers.binance import BinanceCandleProvider

from .models import CopyResult, CopySignal, OpenPosition, SideLeg
from .signal_detector import SignalDetector

if TYPE_CHECKING:
    from collections.abc import Mapping

    from trading_tools.apps.whale_monitor.repository import WhaleRepository
    from trading_tools.clients.polymarket.client import PolymarketClient
    from trading_tools.clients.polymarket.models import MarketToken

    from .config import WhaleCopyConfig

logger = logging.getLogger(__name__)

_HEARTBEAT_INTERVAL = 60
_MIDPOINT_FALLBACK = Decimal("0.50")
_WIN_PRICE = Decimal("1.0")
_LOSS_PRICE = Decimal("0.0")
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

    Poll the database for new whale trades, detect directional bias
    signals, and either log virtual trades (paper mode) or place real
    orders (live mode) via the Polymarket CLOB API.

    Dynamically manage dual-side positions: open new ones, top-up when
    the whale doubles down, flip when the whale reverses direction.

    Attributes:
        config: Immutable service configuration.
        repo: Async repository for whale trade queries.
        live: Enable live trading (requires ``client``).
        client: Authenticated Polymarket client for live orders.

    """

    config: WhaleCopyConfig
    repo: WhaleRepository
    live: bool = False
    client: PolymarketClient | None = None
    _detector: SignalDetector | None = field(default=None, repr=False)
    _binance: BinanceClient | None = field(default=None, repr=False)
    _gamma: GammaClient | None = field(default=None, repr=False)
    _positions: dict[str, OpenPosition] = field(default_factory=_empty_position_dict, repr=False)
    _results: list[CopyResult] = field(default_factory=_empty_result_list, repr=False)
    _poll_count: int = field(default=0, repr=False)
    _running: bool = field(default=False, repr=False)
    _last_heartbeat: float = field(default=0.0, repr=False)

    async def run(self) -> None:
        """Run the polling loop until interrupted.

        Initialise the signal detector and enter a tight async loop that
        polls for signals and handles them. Log a heartbeat every 60
        seconds for CloudWatch monitoring. Catch ``KeyboardInterrupt``
        and ``asyncio.CancelledError`` for graceful shutdown.
        """
        self._detector = SignalDetector(
            repo=self.repo,
            whale_address=self.config.whale_address,
            min_bias=self.config.min_bias,
            min_trades=self.config.min_trades,
            lookback_seconds=self.config.lookback_seconds,
            min_time_to_start=self.config.min_time_to_start,
            max_window_seconds=self.config.max_window_seconds,
        )
        self._binance = BinanceClient()
        self._gamma = GammaClient()
        self._running = True
        mode = "LIVE" if self.live else "PAPER"
        logger.info(
            "whale-copy started mode=%s address=%s poll=%ds"
            " lookback=%ds min_bias=%.1f min_trades=%d"
            " min_time_to_start=%ds capital=$%s max_pos=%s%%"
            " topup_delta=%.1f min_unfavoured=%.0f%%",
            mode,
            self.config.whale_address,
            self.config.poll_interval,
            self.config.lookback_seconds,
            self.config.min_bias,
            self.config.min_trades,
            self.config.min_time_to_start,
            self.config.capital,
            self.config.max_position_pct * 100,
            self.config.topup_bias_delta,
            self.config.min_unfavoured_pct * 100,
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
            await self._gamma.close()

    def stop(self) -> None:
        """Signal the polling loop to stop after the current cycle."""
        self._running = False

    async def _poll_cycle(self) -> None:
        """Execute one poll-detect-act cycle.

        Detect new signals, decide whether to open, top-up, or flip
        each one, and close expired positions.
        """
        assert self._detector is not None  # noqa: S101
        self._poll_count += 1

        signals = await self._detector.detect_signals()
        for signal in signals:
            await self._process_signal(signal)

        await self._close_expired_positions()

    async def _process_signal(self, signal: CopySignal) -> None:
        """Decide the correct action for a signal and execute it.

        Three possible actions:
        - **OPEN**: no existing position for this market.
        - **FLIP**: existing position in the opposite direction.
        - **TOP-UP**: same direction but bias increased by at least
          ``topup_bias_delta``.

        Args:
            signal: The copy signal to process.

        """
        cid = signal.condition_id
        existing = self._positions.get(cid)

        if existing is None:
            await self._open_position(signal)
            return

        if signal.favoured_side != existing.favoured_side:
            await self._flip_position(existing, signal)
            return

        bias_increase = signal.bias_ratio - existing.last_bias
        if bias_increase >= self.config.topup_bias_delta:
            await self._topup_position(existing, signal)

    async def _open_position(self, signal: CopySignal) -> None:
        """Open a new dual-side position for a market not yet traded.

        Args:
            signal: The copy signal to act on.

        """
        now = int(time.time())

        if self.live and self.client is not None:
            await self._open_live(signal, now)
        else:
            await self._open_paper(signal, now)

    async def _open_paper(self, signal: CopySignal, now: int) -> None:
        """Open a paper dual-side position for a new signal.

        Args:
            signal: The copy signal to simulate.
            now: Current UTC epoch seconds.

        """
        prices = await self._fetch_gamma_prices(signal)
        allocation = self._compute_spread_allocation(prices, signal)

        if allocation is None:
            logger.warning("Skipping %s: both sides below minimum", signal.condition_id[:12])
            return

        up_leg, down_leg = allocation
        pos = OpenPosition(
            signal=signal,
            favoured_side=signal.favoured_side,
            up_leg=up_leg,
            down_leg=down_leg,
            entry_time=now,
            last_bias=signal.bias_ratio,
            is_paper=True,
        )
        self._positions[signal.condition_id] = pos
        spread = prices["Up"] + prices["Down"]
        logger.info(
            "PAPER OPEN %s favoured=%s spread=%.4f"
            " up=[%.4f x %.2f] down=[%.4f x %.2f] cost=$%.4f asset=%s bias=%.1f",
            signal.condition_id[:12],
            signal.favoured_side,
            spread,
            up_leg.entry_price if up_leg else ZERO,
            up_leg.quantity if up_leg else ZERO,
            down_leg.entry_price if down_leg else ZERO,
            down_leg.quantity if down_leg else ZERO,
            pos.total_cost_basis,
            signal.asset,
            signal.bias_ratio,
        )

    async def _open_live(self, signal: CopySignal, now: int) -> None:
        """Place real orders for both sides of a new signal.

        Args:
            signal: The copy signal to execute.
            now: Current UTC epoch seconds.

        """
        assert self.client is not None  # noqa: S101

        try:
            market = await self.client.get_market(signal.condition_id)
        except Exception:
            logger.exception("Failed to fetch market %s", signal.condition_id[:12])
            return

        tokens_by_side = {t.outcome: t for t in market.tokens}
        prices = _prices_from_tokens(tokens_by_side)
        allocation = self._compute_spread_allocation(prices, signal)

        if allocation is None:
            logger.warning("Skipping %s: both sides below minimum", signal.condition_id[:12])
            return

        up_leg, down_leg = allocation

        # Place orders for each leg
        up_leg = await self._place_leg_order(up_leg, tokens_by_side.get("Up"))
        down_leg = await self._place_leg_order(down_leg, tokens_by_side.get("Down"))

        if up_leg is None and down_leg is None:
            logger.warning("Both leg orders failed for %s", signal.condition_id[:12])
            return

        pos = OpenPosition(
            signal=signal,
            favoured_side=signal.favoured_side,
            up_leg=up_leg,
            down_leg=down_leg,
            entry_time=now,
            last_bias=signal.bias_ratio,
            is_paper=False,
        )
        self._positions[signal.condition_id] = pos
        logger.info(
            "LIVE OPEN %s favoured=%s cost=$%.4f orders=%s",
            signal.condition_id[:12],
            signal.favoured_side,
            pos.total_cost_basis,
            pos.all_order_ids,
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
        if leg is None or token is None:
            return None

        response = await self._place_order(token.token_id, leg.entry_price, leg.quantity)
        if response is None:
            return None

        leg.order_ids.append(response)
        return leg

    async def _topup_position(self, pos: OpenPosition, signal: CopySignal) -> None:
        """Add to an existing position when whale increases conviction.

        Args:
            pos: The existing open position to top up.
            signal: The new signal with increased bias.

        """
        if self.live and self.client is not None:
            await self._topup_live(pos, signal)
        else:
            await self._topup_paper(pos, signal)

    async def _topup_paper(self, pos: OpenPosition, signal: CopySignal) -> None:
        """Add paper fills to both legs of an existing position.

        Args:
            pos: The existing open position.
            signal: The new signal with increased bias.

        """
        prices = await self._fetch_gamma_prices(signal)
        allocation = self._compute_spread_allocation(prices, signal)

        if allocation is None:
            return

        new_up, new_down = allocation
        if new_up and pos.up_leg:
            pos.up_leg.add_fill(new_up.entry_price, new_up.quantity)
        elif new_up and pos.up_leg is None:
            pos.up_leg = new_up

        if new_down and pos.down_leg:
            pos.down_leg.add_fill(new_down.entry_price, new_down.quantity)
        elif new_down and pos.down_leg is None:
            pos.down_leg = new_down

        old_bias = pos.last_bias
        pos.last_bias = signal.bias_ratio
        pos.signal = signal
        logger.info(
            "PAPER TOPUP %s cost=$%.4f bias=%.1f→%.1f",
            signal.condition_id[:12],
            pos.total_cost_basis,
            old_bias,
            signal.bias_ratio,
        )

    async def _topup_live(self, pos: OpenPosition, signal: CopySignal) -> None:
        """Place live top-up orders for both legs of an existing position.

        Args:
            pos: The existing open position.
            signal: The new signal with increased bias.

        """
        assert self.client is not None  # noqa: S101

        try:
            market = await self.client.get_market(signal.condition_id)
        except Exception:
            logger.exception("Failed to fetch market for top-up %s", signal.condition_id[:12])
            return

        tokens_by_side = {t.outcome: t for t in market.tokens}
        prices = _prices_from_tokens(tokens_by_side)
        allocation = self._compute_spread_allocation(prices, signal)

        if allocation is None:
            return

        new_up, new_down = allocation

        if new_up:
            up_token = tokens_by_side.get("Up")
            if up_token:
                resp = await self._place_order(
                    up_token.token_id, new_up.entry_price, new_up.quantity
                )
                if resp:
                    if pos.up_leg:
                        pos.up_leg.add_fill(new_up.entry_price, new_up.quantity)
                        pos.up_leg.order_ids.append(resp)
                    else:
                        new_up.order_ids.append(resp)
                        pos.up_leg = new_up

        if new_down:
            down_token = tokens_by_side.get("Down")
            if down_token:
                resp = await self._place_order(
                    down_token.token_id, new_down.entry_price, new_down.quantity
                )
                if resp:
                    if pos.down_leg:
                        pos.down_leg.add_fill(new_down.entry_price, new_down.quantity)
                        pos.down_leg.order_ids.append(resp)
                    else:
                        new_down.order_ids.append(resp)
                        pos.down_leg = new_down

        pos.last_bias = signal.bias_ratio
        pos.signal = signal
        logger.info(
            "LIVE TOPUP %s cost=$%.4f orders=%s",
            signal.condition_id[:12],
            pos.total_cost_basis,
            pos.all_order_ids,
        )

    async def _flip_position(self, pos: OpenPosition, signal: CopySignal) -> None:
        """Close existing dual-side position and open in the opposite direction.

        The whale has reversed their directional bet. Close the current
        position at a total loss of cost basis and open a new dual-side
        position in the new direction.

        Args:
            pos: The existing position to close.
            signal: The new signal in the opposite direction.

        """
        cid = signal.condition_id
        pnl = -pos.total_cost_basis
        now = int(time.time())

        closed = CopyResult(
            signal=pos.signal,
            favoured_side=pos.favoured_side,
            up_entry=pos.up_leg.entry_price if pos.up_leg else None,
            up_qty=pos.up_leg.quantity if pos.up_leg else None,
            down_entry=pos.down_leg.entry_price if pos.down_leg else None,
            down_qty=pos.down_leg.quantity if pos.down_leg else None,
            total_cost_basis=pos.total_cost_basis,
            entry_time=pos.entry_time,
            exit_time=now,
            pnl=pnl,
            is_paper=pos.is_paper,
            order_ids=tuple(pos.all_order_ids),
        )
        self._results.append(closed)
        del self._positions[cid]

        logger.info(
            "%s FLIP %s %s→%s pnl=%.4f (closing old side)",
            "PAPER" if pos.is_paper else "LIVE",
            cid[:12],
            pos.favoured_side,
            signal.favoured_side,
            pnl,
        )

        await self._open_position(signal)

    async def _place_order(self, token_id: str, price: Decimal, quantity: Decimal) -> str | None:
        """Place an order and return the order ID, or None on failure.

        Args:
            token_id: CLOB token ID for the outcome.
            price: Order price.
            quantity: Order size in tokens.

        Returns:
            Order ID string, or ``None`` if the order failed.

        """
        assert self.client is not None  # noqa: S101

        order_type = "market" if self.config.use_market_orders else "limit"
        request = OrderRequest(
            token_id=token_id,
            side="BUY",
            price=price,
            size=quantity,
            order_type=order_type,
        )

        try:
            response = await self.client.place_order(request)
        except Exception:
            logger.exception("Order failed for token %s", token_id[:12])
            return None

        return response.order_id

    def _compute_spread_allocation(
        self,
        prices: dict[str, Decimal],
        signal: CopySignal,
    ) -> tuple[SideLeg | None, SideLeg | None] | None:
        """Compute dual-side allocation split by whale volume percentages.

        Scale the base position by bias, split by the whale's Up/Down
        allocation, enforce the ``min_unfavoured_pct`` floor, and convert
        to token quantities. Legs below ``_MIN_TOKEN_QTY`` (5 tokens)
        are dropped. Return ``None`` if both legs are below minimum.

        Args:
            prices: Token prices keyed by side (``"Up"`` and ``"Down"``).
            signal: The copy signal with volume allocation split.

        Returns:
            Tuple of (up_leg, down_leg) or ``None`` if both below minimum.

        """
        up_price = prices.get("Up", _MIDPOINT_FALLBACK)
        down_price = prices.get("Down", _MIDPOINT_FALLBACK)

        if up_price <= ZERO or down_price <= ZERO:
            return None

        base_spend = self.config.capital * self.config.max_position_pct
        scale = Decimal("1.0")
        if signal.bias_ratio > ZERO and self.config.min_bias > ZERO:
            scale = min(signal.bias_ratio / self.config.min_bias, self.config.max_bias_scale)
        total_spend = base_spend * scale

        # Apply whale volume split with min_unfavoured_pct floor
        up_pct = signal.up_volume_pct
        down_pct = signal.down_volume_pct

        unfavoured_pct = down_pct if signal.favoured_side == "Up" else up_pct
        floor = self.config.min_unfavoured_pct

        if unfavoured_pct < floor:
            if signal.favoured_side == "Up":
                down_pct = floor
                up_pct = _ONE - floor
            else:
                up_pct = floor
                down_pct = _ONE - floor

        up_spend = total_spend * up_pct
        down_spend = total_spend * down_pct

        up_qty = (up_spend / up_price).quantize(Decimal("0.01"))
        down_qty = (down_spend / down_price).quantize(Decimal("0.01"))

        up_leg = (
            SideLeg(side="Up", entry_price=up_price, quantity=up_qty, cost_basis=up_spend)
            if up_qty >= _MIN_TOKEN_QTY
            else None
        )
        down_leg = (
            SideLeg(side="Down", entry_price=down_price, quantity=down_qty, cost_basis=down_spend)
            if down_qty >= _MIN_TOKEN_QTY
            else None
        )

        spread = up_price + down_price
        logger.info(
            "  SPREAD %s up_pct=%.0f%% down_pct=%.0f%% spread=%.4f up_qty=%.2f down_qty=%.2f",
            signal.condition_id[:12],
            up_pct * 100,
            down_pct * 100,
            spread,
            up_qty,
            down_qty,
        )

        if up_leg is None and down_leg is None:
            return None

        return up_leg, down_leg

    async def _fetch_gamma_prices(self, signal: CopySignal) -> dict[str, Decimal]:
        """Fetch token prices for both sides from the Gamma API.

        Query the public Polymarket Gamma API for market data and extract
        both Up and Down outcome prices. If only one side is found, derive
        the other as ``1.0 - price``. Fall back to midpoints on failure.

        Args:
            signal: The copy signal with condition_id.

        Returns:
            Dict mapping ``"Up"`` and ``"Down"`` to their token prices.

        """
        assert self._gamma is not None  # noqa: S101

        try:
            market = await self._gamma.get_market(signal.condition_id)
        except Exception:
            logger.warning("Gamma API failed for %s, using midpoint", signal.condition_id[:12])
            return {"Up": _MIDPOINT_FALLBACK, "Down": _MIDPOINT_FALLBACK}

        return _parse_gamma_prices(market)

    async def _close_expired_positions(self) -> None:
        """Close positions whose market windows have expired.

        Fetch actual spot price from Binance 1-min candles to determine
        whether the whale's directional bet was correct. For dual-side
        positions, P&L = winning_qty - total_cost_basis.
        """
        now = int(time.time())
        expired = [cid for cid, pos in self._positions.items() if pos.signal.window_end_ts <= now]

        for cid in expired:
            pos = self._positions.pop(cid)
            winning_side = await self._resolve_outcome(pos)
            pnl = _compute_dual_side_pnl(pos, winning_side)

            closed = CopyResult(
                signal=pos.signal,
                favoured_side=pos.favoured_side,
                up_entry=pos.up_leg.entry_price if pos.up_leg else None,
                up_qty=pos.up_leg.quantity if pos.up_leg else None,
                down_entry=pos.down_leg.entry_price if pos.down_leg else None,
                down_qty=pos.down_leg.quantity if pos.down_leg else None,
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
                "%s CLOSE %s %s pnl=%.4f cost=$%.4f winning=%s favoured=%s asset=%s",
                "PAPER" if pos.is_paper else "LIVE",
                cid[:12],
                outcome,
                pnl,
                pos.total_cost_basis,
                winning_side,
                pos.favoured_side,
                pos.signal.asset,
            )

    async def _resolve_outcome(self, pos: OpenPosition) -> str:
        """Determine which side won based on Binance spot price movement.

        Fetch Binance 1-min candles for the market window and compare
        the opening price against the closing price. Return ``"Up"`` if
        price went up, ``"Down"`` otherwise.

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
            "  SPOT %s open=%.2f close=%.2f direction=%s favoured=%s",
            signal.asset,
            open_price,
            close_price,
            winning_side,
            pos.favoured_side,
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
            logger.info(
                "HEARTBEAT polls=%d window_trades=%d open=%d closed=%d pnl=$%.2f",
                self._poll_count,
                window_trades,
                len(self._positions),
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


def _compute_dual_side_pnl(pos: OpenPosition, winning_side: str) -> Decimal:
    """Compute P&L for a dual-side position at resolution.

    The winning side pays $1.00 per token, the losing side pays $0.00.
    P&L = winning_leg_quantity - total_cost_basis.

    Args:
        pos: The open position with up/down legs.
        winning_side: Which side won (``"Up"`` or ``"Down"``).

    Returns:
        Realised P&L in USDC.

    """
    winning_qty = ZERO
    if winning_side == "Up" and pos.up_leg:
        winning_qty = pos.up_leg.quantity
    elif winning_side == "Down" and pos.down_leg:
        winning_qty = pos.down_leg.quantity

    return winning_qty - pos.total_cost_basis


def _parse_gamma_prices(market: Mapping[str, object]) -> dict[str, Decimal]:
    """Extract token prices for both sides from Gamma API market data.

    Parse the ``outcomes`` and ``outcomePrices`` fields (both JSON-encoded
    strings) to build a dict of side→price. If only one side is found,
    derive the other as ``1.0 - price``. Fall back to midpoints on failure.

    Args:
        market: Raw market dictionary from ``GammaClient.get_market()``.

    Returns:
        Dict mapping ``"Up"`` and ``"Down"`` to their Decimal prices.

    """
    try:
        outcomes_raw = market.get("outcomes")
        prices_raw = market.get("outcomePrices")

        if not isinstance(outcomes_raw, str) or not isinstance(prices_raw, str):
            return {"Up": _MIDPOINT_FALLBACK, "Down": _MIDPOINT_FALLBACK}

        outcomes: list[str] = json.loads(outcomes_raw)
        prices_list: list[str] = json.loads(prices_raw)

        result: dict[str, Decimal] = {}
        for outcome, price_str in zip(outcomes, prices_list, strict=False):
            price = Decimal(price_str)
            if price > ZERO:
                result[outcome] = price

        # Derive missing side from the other
        if "Up" in result and "Down" not in result:
            result["Down"] = _ONE - result["Up"]
        elif "Down" in result and "Up" not in result:
            result["Up"] = _ONE - result["Down"]

        if "Up" in result and "Down" in result:
            return result
    except (json.JSONDecodeError, ValueError, TypeError):
        pass

    return {"Up": _MIDPOINT_FALLBACK, "Down": _MIDPOINT_FALLBACK}


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

    if "Up" not in prices:
        prices["Up"] = _MIDPOINT_FALLBACK
    if "Down" not in prices:
        prices["Down"] = _MIDPOINT_FALLBACK

    return prices
