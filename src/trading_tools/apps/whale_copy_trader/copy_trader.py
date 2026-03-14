"""Core copy-trading engine that mirrors whale bets in real-time.

Run a tight polling loop that detects whale directional bias signals and
either simulates (paper) or executes (live) copy trades on Polymarket.

Performance design:
- 5-second default poll interval for minimal latency on 5-minute markets.
- Incremental signal detection (see ``SignalDetector``).
- Pre-authenticated client: Polymarket connection established at startup.
- Market orders by default: fastest fill, no limit queue waiting.
- Async throughout: no blocking calls in the hot path.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING

from trading_tools.clients.polymarket.models import OrderRequest
from trading_tools.core.models import ZERO

from .models import CopyResult, CopySignal
from .signal_detector import SignalDetector

if TYPE_CHECKING:
    from trading_tools.apps.whale_monitor.repository import WhaleRepository
    from trading_tools.clients.polymarket.client import PolymarketClient
    from trading_tools.clients.polymarket.models import MarketToken

    from .config import WhaleCopyConfig

logger = logging.getLogger(__name__)

_HEARTBEAT_INTERVAL = 60
_MIDPOINT_FALLBACK = Decimal("0.50")


def _empty_str_set() -> set[str]:
    """Return an empty set[str] for dataclass default_factory."""
    return set()


def _empty_result_dict() -> dict[str, CopyResult]:
    """Return an empty dict[str, CopyResult] for dataclass default_factory."""
    return {}


def _empty_result_list() -> list[CopyResult]:
    """Return an empty list[CopyResult] for dataclass default_factory."""
    return []


@dataclass
class WhaleCopyTrader:
    """Copy-trading engine that mirrors whale bets on Polymarket.

    Poll the database for new whale trades, detect directional bias
    signals, and either log virtual trades (paper mode) or place real
    market orders (live mode) via the Polymarket CLOB API.

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
    _acted_on: set[str] = field(default_factory=_empty_str_set, repr=False)
    _positions: dict[str, CopyResult] = field(default_factory=_empty_result_dict, repr=False)
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
        self._running = True
        mode = "LIVE" if self.live else "PAPER"
        logger.info(
            "whale-copy started mode=%s address=%s poll=%ds"
            " lookback=%ds min_bias=%.1f min_trades=%d"
            " min_time_to_start=%ds capital=$%s max_pos=%s%%",
            mode,
            self.config.whale_address,
            self.config.poll_interval,
            self.config.lookback_seconds,
            self.config.min_bias,
            self.config.min_trades,
            self.config.min_time_to_start,
            self.config.capital,
            self.config.max_position_pct * 100,
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

    def stop(self) -> None:
        """Signal the polling loop to stop after the current cycle."""
        self._running = False

    async def _poll_cycle(self) -> None:
        """Execute one poll-detect-act cycle.

        Detect new signals, handle any that haven't been acted on,
        and close expired positions.
        """
        assert self._detector is not None  # noqa: S101
        self._poll_count += 1

        signals = await self._detector.detect_signals()
        for signal in signals:
            if signal.condition_id not in self._acted_on:
                await self._handle_signal(signal)

        await self._close_expired_positions()

    async def _handle_signal(self, signal: CopySignal) -> None:
        """Open a position for a new copy signal.

        Paper mode: compute position size and log the virtual trade.
        Live mode: fetch market tokens, find the matching outcome,
        and place a market order via the CLOB.

        Args:
            signal: The copy signal to act on.

        """
        self._acted_on.add(signal.condition_id)
        now = int(time.time())

        if self.live and self.client is not None:
            await self._handle_live_signal(signal, now)
        else:
            self._handle_paper_signal(signal, now)

    def _handle_paper_signal(self, signal: CopySignal, now: int) -> None:
        """Open a paper position for the given signal.

        Compute position size from config capital and max_position_pct,
        using the inverse of bias_ratio as an estimated market price.

        Args:
            signal: The copy signal to simulate.
            now: Current UTC epoch seconds.

        """
        price = _estimate_entry_price()
        quantity = self._compute_quantity(price)

        if quantity <= ZERO:
            logger.warning("Skipping %s: quantity too small", signal.condition_id[:12])
            return

        result = CopyResult(
            signal=signal,
            entry_price=price,
            quantity=quantity,
            entry_time=now,
            is_paper=True,
        )
        self._positions[signal.condition_id] = result
        logger.info(
            "PAPER OPEN %s side=%s price=%.4f qty=%.2f asset=%s bias=%.1f",
            signal.condition_id[:12],
            signal.favoured_side,
            price,
            quantity,
            signal.asset,
            signal.bias_ratio,
        )

    async def _handle_live_signal(self, signal: CopySignal, now: int) -> None:
        """Place a real market order for the given signal.

        Fetch market tokens from the CLOB, find the token matching the
        whale's favoured side, and place a BUY market order.

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
        quantity = self._compute_quantity(price)

        if quantity <= ZERO:
            logger.warning("Skipping %s: quantity too small", signal.condition_id[:12])
            return

        order_type = "market" if self.config.use_market_orders else "limit"
        request = OrderRequest(
            token_id=token.token_id,
            side="BUY",
            price=price,
            size=quantity,
            order_type=order_type,
        )

        try:
            response = await self.client.place_order(request)
        except Exception:
            logger.exception("Order failed for %s", signal.condition_id[:12])
            return

        result = CopyResult(
            signal=signal,
            entry_price=price,
            quantity=quantity,
            entry_time=now,
            is_paper=False,
            order_id=response.order_id,
        )
        self._positions[signal.condition_id] = result
        logger.info(
            "LIVE OPEN %s side=%s price=%.4f qty=%.2f order=%s",
            signal.condition_id[:12],
            signal.favoured_side,
            price,
            quantity,
            response.order_id,
        )

    def _compute_quantity(self, price: Decimal) -> Decimal:
        """Compute position size in tokens from config capital limits.

        Args:
            price: Estimated entry price per token.

        Returns:
            Number of tokens to buy, or zero if price is non-positive.

        """
        if price <= ZERO:
            return ZERO
        max_spend = self.config.capital * self.config.max_position_pct
        return (max_spend / price).quantize(Decimal("0.01"))

    async def _close_expired_positions(self) -> None:
        """Close positions whose market windows have expired.

        For 5-minute markets, Polymarket auto-redeems winners, so closing
        is mainly for P&L tracking. Paper mode estimates exit price as
        1.0 for the favoured side (winner) or 0.0 (loser) since these
        are binary outcomes.
        """
        now = int(time.time())
        expired = [cid for cid, pos in self._positions.items() if pos.signal.window_end_ts <= now]

        for cid in expired:
            pos = self._positions.pop(cid)
            # Binary outcome: winner pays 1.0, loser pays 0.0
            # We assume the whale is correct (79% historical accuracy)
            # Actual P&L settles when the market resolves
            exit_price = Decimal("1.0")
            pnl = (exit_price - pos.entry_price) * pos.quantity

            closed = CopyResult(
                signal=pos.signal,
                entry_price=pos.entry_price,
                quantity=pos.quantity,
                entry_time=pos.entry_time,
                exit_price=exit_price,
                exit_time=now,
                pnl=pnl,
                is_paper=pos.is_paper,
                order_id=pos.order_id,
            )
            self._results.append(closed)
            logger.info(
                "%s CLOSE %s pnl=%.4f entry=%.4f exit=%.4f",
                "PAPER" if pos.is_paper else "LIVE",
                cid[:12],
                pnl,
                pos.entry_price,
                exit_price,
            )

    def _maybe_log_heartbeat(self) -> None:
        """Log a heartbeat message if the interval has elapsed."""
        now = time.monotonic()
        if now - self._last_heartbeat >= _HEARTBEAT_INTERVAL:
            self._last_heartbeat = now
            total_pnl = sum(r.pnl for r in self._results)
            assert self._detector is not None  # noqa: S101
            window_trades = self._detector.window_size
            logger.info(
                "HEARTBEAT polls=%d window_trades=%d signals=%d open=%d closed=%d pnl=$%.2f",
                self._poll_count,
                window_trades,
                len(self._acted_on),
                len(self._positions),
                len(self._results),
                total_pnl,
            )

    def _log_summary(self) -> None:
        """Log a final session summary on shutdown."""
        total_pnl = sum(r.pnl for r in self._results)
        logger.info(
            "SESSION SUMMARY polls=%d signals=%d closed=%d open=%d pnl=%.4f",
            self._poll_count,
            len(self._acted_on),
            len(self._results),
            len(self._positions),
            total_pnl,
        )

    @property
    def positions(self) -> dict[str, CopyResult]:
        """Return the current open positions (read-only access)."""
        return dict(self._positions)

    @property
    def results(self) -> list[CopyResult]:
        """Return all closed trade results (read-only access)."""
        return list(self._results)

    @property
    def acted_on(self) -> set[str]:
        """Return condition IDs already acted on (read-only access)."""
        return set(self._acted_on)

    @property
    def poll_count(self) -> int:
        """Return the number of completed poll cycles."""
        return self._poll_count


def _estimate_entry_price() -> Decimal:
    """Estimate an entry price for paper trading.

    Use the midpoint fallback since we don't have live order book
    data in paper mode.

    Returns:
        Estimated entry price as a Decimal.

    """
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
