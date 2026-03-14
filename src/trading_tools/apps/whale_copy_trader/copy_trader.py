"""Core copy-trading engine that mirrors whale bets in real-time.

Run a tight polling loop that detects whale directional bias signals and
either simulates (paper) or executes (live) copy trades on Polymarket.

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

from .models import CopyResult, CopySignal, OpenPosition
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

    Dynamically manage positions: open new ones, top-up when the whale
    doubles down, flip when the whale reverses direction.

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
        )
        self._binance = BinanceClient()
        self._gamma = GammaClient()
        self._running = True
        mode = "LIVE" if self.live else "PAPER"
        logger.info(
            "whale-copy started mode=%s address=%s poll=%ds"
            " lookback=%ds min_bias=%.1f min_trades=%d"
            " min_time_to_start=%ds capital=$%s max_pos=%s%%"
            " topup_delta=%.1f",
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

        if signal.favoured_side != existing.side:
            await self._flip_position(existing, signal)
            return

        bias_increase = signal.bias_ratio - existing.last_bias
        if bias_increase >= self.config.topup_bias_delta:
            await self._topup_position(existing, signal)

    async def _open_position(self, signal: CopySignal) -> None:
        """Open a new position for a market not yet traded.

        Args:
            signal: The copy signal to act on.

        """
        now = int(time.time())

        if self.live and self.client is not None:
            await self._open_live(signal, now)
        else:
            await self._open_paper(signal, now)

    async def _open_paper(self, signal: CopySignal, now: int) -> None:
        """Open a paper position for a new signal.

        Args:
            signal: The copy signal to simulate.
            now: Current UTC epoch seconds.

        """
        price = await self._fetch_gamma_price(signal)
        quantity = self._compute_quantity(price, signal.bias_ratio)

        if quantity <= ZERO:
            logger.warning("Skipping %s: quantity too small", signal.condition_id[:12])
            return

        pos = OpenPosition(
            signal=signal,
            side=signal.favoured_side,
            entry_price=price,
            quantity=quantity,
            cost_basis=price * quantity,
            entry_time=now,
            last_bias=signal.bias_ratio,
            is_paper=True,
        )
        self._positions[signal.condition_id] = pos
        logger.info(
            "PAPER OPEN %s side=%s price=%.4f qty=%.2f asset=%s bias=%.1f",
            signal.condition_id[:12],
            signal.favoured_side,
            price,
            quantity,
            signal.asset,
            signal.bias_ratio,
        )

    async def _open_live(self, signal: CopySignal, now: int) -> None:
        """Place a real market order for a new signal.

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

        token = _find_token_for_side(market.tokens, signal.favoured_side)
        if token is None:
            logger.warning(
                "No token found for side=%s in %s", signal.favoured_side, signal.condition_id[:12]
            )
            return

        price = token.price if token.price > ZERO else _MIDPOINT_FALLBACK
        quantity = self._compute_quantity(price, signal.bias_ratio)

        if quantity <= ZERO:
            logger.warning("Skipping %s: quantity too small", signal.condition_id[:12])
            return

        response = await self._place_order(token.token_id, price, quantity)
        if response is None:
            return

        pos = OpenPosition(
            signal=signal,
            side=signal.favoured_side,
            entry_price=price,
            quantity=quantity,
            cost_basis=price * quantity,
            entry_time=now,
            last_bias=signal.bias_ratio,
            is_paper=False,
            order_ids=[response],
        )
        self._positions[signal.condition_id] = pos
        logger.info(
            "LIVE OPEN %s side=%s price=%.4f qty=%.2f order=%s",
            signal.condition_id[:12],
            signal.favoured_side,
            price,
            quantity,
            response,
        )

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
        """Add a paper fill to an existing position.

        Args:
            pos: The existing open position.
            signal: The new signal with increased bias.

        """
        price = await self._fetch_gamma_price(signal)
        quantity = self._compute_quantity(price, signal.bias_ratio)

        if quantity <= ZERO:
            return

        pos.add_fill(price, quantity)
        pos.last_bias = signal.bias_ratio
        pos.signal = signal
        logger.info(
            "PAPER TOPUP %s side=%s price=%.4f +qty=%.2f total_qty=%.2f bias=%.1f→%.1f",
            signal.condition_id[:12],
            signal.favoured_side,
            price,
            quantity,
            pos.quantity,
            pos.last_bias - (signal.bias_ratio - pos.last_bias),
            signal.bias_ratio,
        )

    async def _topup_live(self, pos: OpenPosition, signal: CopySignal) -> None:
        """Place a live top-up order for an existing position.

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

        token = _find_token_for_side(market.tokens, signal.favoured_side)
        if token is None:
            return

        price = token.price if token.price > ZERO else _MIDPOINT_FALLBACK
        quantity = self._compute_quantity(price, signal.bias_ratio)

        if quantity <= ZERO:
            return

        response = await self._place_order(token.token_id, price, quantity)
        if response is None:
            return

        pos.add_fill(price, quantity)
        pos.last_bias = signal.bias_ratio
        pos.signal = signal
        pos.order_ids.append(response)
        logger.info(
            "LIVE TOPUP %s side=%s price=%.4f +qty=%.2f total_qty=%.2f order=%s",
            signal.condition_id[:12],
            signal.favoured_side,
            price,
            quantity,
            pos.quantity,
            response,
        )

    async def _flip_position(self, pos: OpenPosition, signal: CopySignal) -> None:
        """Close existing position and open in the opposite direction.

        The whale has reversed their directional bet. Close the current
        position at a loss (exit=0.0 since we're on the wrong side now)
        and open a new position in the new direction.

        Args:
            pos: The existing position to close.
            signal: The new signal in the opposite direction.

        """
        cid = signal.condition_id
        exit_price = _LOSS_PRICE
        pnl = (exit_price - pos.entry_price) * pos.quantity
        now = int(time.time())

        closed = CopyResult(
            signal=pos.signal,
            side=pos.side,
            entry_price=pos.entry_price,
            quantity=pos.quantity,
            entry_time=pos.entry_time,
            exit_price=exit_price,
            exit_time=now,
            pnl=pnl,
            is_paper=pos.is_paper,
            order_ids=tuple(pos.order_ids),
        )
        self._results.append(closed)
        del self._positions[cid]

        logger.info(
            "%s FLIP %s %s→%s pnl=%.4f (closing old side)",
            "PAPER" if pos.is_paper else "LIVE",
            cid[:12],
            pos.side,
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

    def _compute_quantity(self, price: Decimal, bias_ratio: Decimal = ZERO) -> Decimal:
        """Compute bias-scaled position size in tokens.

        Scale the base position (capital * max_position_pct) by the whale's
        conviction strength. Stronger bias = bigger bet, capped at
        ``config.max_bias_scale``.

        Args:
            price: Entry price per token.
            bias_ratio: Whale's bias ratio for this signal.

        Returns:
            Number of tokens to buy, or zero if price is non-positive.

        """
        if price <= ZERO:
            return ZERO
        base_spend = self.config.capital * self.config.max_position_pct

        scale = Decimal("1.0")
        if bias_ratio > ZERO and self.config.min_bias > ZERO:
            scale = min(bias_ratio / self.config.min_bias, self.config.max_bias_scale)

        spend = base_spend * scale
        return (spend / price).quantize(Decimal("0.01"))

    async def _fetch_gamma_price(self, signal: CopySignal) -> Decimal:
        """Fetch the real token price for the favoured side from the Gamma API.

        Query the public Polymarket Gamma API for market data and extract
        the outcome price matching the whale's favoured side. Fall back to
        the midpoint if the API call fails or the side cannot be matched.

        Args:
            signal: The copy signal with condition_id and favoured_side.

        Returns:
            Token price as a Decimal.

        """
        assert self._gamma is not None  # noqa: S101

        try:
            market = await self._gamma.get_market(signal.condition_id)
        except Exception:
            logger.warning("Gamma API failed for %s, using midpoint", signal.condition_id[:12])
            return _MIDPOINT_FALLBACK

        return _parse_gamma_price(market, signal.favoured_side)

    async def _close_expired_positions(self) -> None:
        """Close positions whose market windows have expired.

        Fetch actual spot price from Binance 1-min candles to determine
        whether the whale's directional bet was correct. Binary outcome:
        1.0 if correct (win), 0.0 if wrong (loss).
        """
        now = int(time.time())
        expired = [cid for cid, pos in self._positions.items() if pos.signal.window_end_ts <= now]

        for cid in expired:
            pos = self._positions.pop(cid)
            exit_price = await self._resolve_outcome(pos)
            pnl = (exit_price - pos.entry_price) * pos.quantity

            closed = CopyResult(
                signal=pos.signal,
                side=pos.side,
                entry_price=pos.entry_price,
                quantity=pos.quantity,
                entry_time=pos.entry_time,
                exit_price=exit_price,
                exit_time=now,
                pnl=pnl,
                is_paper=pos.is_paper,
                order_ids=tuple(pos.order_ids),
            )
            self._results.append(closed)
            outcome = "WIN" if exit_price == _WIN_PRICE else "LOSS"
            logger.info(
                "%s CLOSE %s %s pnl=%.4f entry=%.4f exit=%.4f side=%s asset=%s",
                "PAPER" if pos.is_paper else "LIVE",
                cid[:12],
                outcome,
                pnl,
                pos.entry_price,
                exit_price,
                pos.side,
                pos.signal.asset,
            )

    async def _resolve_outcome(self, pos: OpenPosition) -> Decimal:
        """Determine whether the whale's directional bet was correct.

        Fetch Binance 1-min candles for the market window and compare
        the opening price against the closing price. If the price moved
        in the whale's favoured direction, the bet wins (1.0); otherwise
        it loses (0.0).

        Args:
            pos: The open position with signal, side, and window timestamps.

        Returns:
            ``Decimal("1.0")`` for a correct bet, ``Decimal("0.0")`` otherwise.

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
            logger.warning("Failed to fetch candles for %s, assuming win", signal.asset)
            return _WIN_PRICE

        if not candles:
            logger.warning("No candles for %s window, assuming win", signal.asset)
            return _WIN_PRICE

        open_price = candles[0].open
        close_price = candles[-1].close
        price_went_up = close_price > open_price

        whale_correct = (pos.side == "Up" and price_went_up) or (
            pos.side == "Down" and not price_went_up
        )

        logger.info(
            "  SPOT %s open=%.2f close=%.2f direction=%s whale_side=%s → %s",
            signal.asset,
            open_price,
            close_price,
            "Up" if price_went_up else "Down",
            pos.side,
            "CORRECT" if whale_correct else "WRONG",
        )

        return _WIN_PRICE if whale_correct else _LOSS_PRICE

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


def _parse_gamma_price(market: Mapping[str, object], favoured_side: str) -> Decimal:
    """Extract the token price for a given side from Gamma API market data.

    Parse the ``outcomes`` and ``outcomePrices`` fields (both JSON-encoded
    strings) to find the price matching the favoured side. Return the
    midpoint fallback if parsing fails or the side is not found.

    Args:
        market: Raw market dictionary from ``GammaClient.get_market()``.
        favoured_side: ``"Up"`` or ``"Down"``.

    Returns:
        Token price for the favoured side, or ``0.50`` as fallback.

    """
    try:
        outcomes_raw = market.get("outcomes")
        prices_raw = market.get("outcomePrices")

        if not isinstance(outcomes_raw, str) or not isinstance(prices_raw, str):
            return _MIDPOINT_FALLBACK

        outcomes: list[str] = json.loads(outcomes_raw)
        prices: list[str] = json.loads(prices_raw)

        for outcome, price_str in zip(outcomes, prices, strict=False):
            if outcome == favoured_side:
                price = Decimal(price_str)
                if price > ZERO:
                    return price
    except (json.JSONDecodeError, ValueError, TypeError):
        pass

    return _MIDPOINT_FALLBACK


def _find_token_for_side(
    tokens: tuple[MarketToken, ...],
    favoured_side: str,
) -> MarketToken | None:
    """Find the market token matching the favoured side.

    Args:
        tokens: Tuple of ``MarketToken`` objects from the CLOB.
        favoured_side: ``"Up"`` or ``"Down"``.

    Returns:
        The matching ``MarketToken``, or ``None`` if not found.

    """
    for token in tokens:
        if token.outcome == favoured_side:
            return token
    return None
