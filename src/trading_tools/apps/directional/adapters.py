"""Concrete adapters for the directional trading engine ports.

Provide live, paper, and backtest implementations of ``ExecutionPort``
and ``MarketDataPort``.  The live execution adapter wraps
``OrderExecutor`` and ``BalanceManager`` for real CLOB order placement.
The paper execution adapter maintains virtual capital with configurable
slippage.  The backtest execution adapter computes fills from historical
order books.  The replay market data adapter serves pre-loaded snapshots
with a controllable clock.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

import httpx

from trading_tools.core.models import ZERO

from .ports import FillResult

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from trading_tools.apps.bot_framework.balance_manager import BalanceManager
    from trading_tools.apps.bot_framework.order_executor import OrderExecutor
    from trading_tools.clients.polymarket.models import OrderBook
    from trading_tools.core.models import Candle

    from .models import MarketOpportunity, TickSample

logger = logging.getLogger(__name__)


@dataclass
class LiveExecution:
    """Execute fills via the Polymarket CLOB with real orders.

    Wrap ``OrderExecutor`` for order placement and ``BalanceManager``
    for capital queries.  Unlike paper mode, capital is not tracked
    internally — the CLOB balance auto-updates as positions settle.

    Attributes:
        executor: Order executor for placing real CLOB orders.
        balance_manager: Manages and caches the real USDC balance.
        committed_capital_fn: Callable that returns current committed
            capital across all open positions.

    """

    executor: OrderExecutor
    balance_manager: BalanceManager
    committed_capital_fn: Callable[[], Decimal]

    async def execute_fill(
        self,
        token_id: str,
        side: str,  # noqa: ARG002
        price: Decimal,
        quantity: Decimal,
    ) -> FillResult | None:
        """Place a real order via the CLOB and return the fill result.

        Args:
            token_id: CLOB token identifier.
            side: Trade direction (always ``"BUY"``).
            price: Desired execution price.
            quantity: Desired token quantity.

        Returns:
            A ``FillResult`` on success, ``None`` if the order failed
            or was not filled.

        """
        try:
            resp = await self.executor.place_order(token_id, "BUY", price, quantity)
            if resp is None or resp.filled <= ZERO:
                return None
            return FillResult(
                price=resp.price,
                quantity=resp.filled,
                order_id=resp.order_id,
            )
        except (httpx.HTTPError, Exception):
            logger.debug("Live fill failed for %s", token_id[:12])
            return None

    def get_capital(self) -> Decimal:
        """Return available USDC balance from the balance manager.

        Returns:
            Available capital in USDC.

        """
        return self.balance_manager.balance

    def total_capital(self) -> Decimal:
        """Return total capital (balance + committed).

        Returns:
            Total capital in USDC.

        """
        return self.balance_manager.balance + self.committed_capital_fn()


class PaperExecution:
    """Virtual capital execution adapter for paper trading.

    Simulate order fills with configurable slippage.  Track capital
    as it is allocated to positions and returned upon settlement.

    Args:
        capital: Starting capital in USDC.
        slippage_pct: Simulated slippage as a decimal fraction
            (e.g. ``0.005`` for 0.5%).

    """

    def __init__(self, capital: Decimal, slippage_pct: Decimal = Decimal("0.005")) -> None:
        """Initialize paper execution with starting capital and slippage.

        Args:
            capital: Starting USDC capital.
            slippage_pct: Slippage fraction applied to fill prices.

        """
        self._capital = capital
        self._total_capital = capital
        self._slippage_pct = slippage_pct

    async def execute_fill(
        self,
        token_id: str,  # noqa: ARG002
        side: str,  # noqa: ARG002
        price: Decimal,
        quantity: Decimal,
    ) -> FillResult | None:
        """Simulate a fill with slippage applied to the price.

        Deduct the fill cost from available capital.  Return ``None``
        if insufficient capital.

        Args:
            token_id: CLOB token identifier (unused in paper mode).
            side: Order side (unused — always buying).
            price: Requested execution price.
            quantity: Requested token quantity.

        Returns:
            ``FillResult`` on success, ``None`` if capital is insufficient.

        """
        slipped_price = price * (Decimal(1) + self._slippage_pct)
        cost = slipped_price * quantity
        if cost > self._capital:
            logger.debug("Paper fill rejected: cost %.4f > capital %.4f", cost, self._capital)
            return None
        self._capital -= cost
        return FillResult(price=slipped_price, quantity=quantity)

    def get_capital(self) -> Decimal:
        """Return available paper capital."""
        return self._capital

    def total_capital(self) -> Decimal:
        """Return total paper capital (available + committed)."""
        return self._total_capital

    def add_capital(self, amount: Decimal) -> None:
        """Add realised P&L back to available capital.

        Args:
            amount: USDC to add (positive for profit, negative for loss).

        """
        self._capital += amount
        self._total_capital += amount


class BacktestExecution:
    """Execution adapter for backtesting with deterministic fills.

    Fill at the requested price with no slippage.  Track capital
    identically to paper mode but without randomness.

    Args:
        capital: Starting capital in USDC.

    """

    def __init__(self, capital: Decimal) -> None:
        """Initialize backtest execution with starting capital.

        Args:
            capital: Starting USDC capital.

        """
        self._capital = capital
        self._total_capital = capital

    async def execute_fill(
        self,
        token_id: str,  # noqa: ARG002
        side: str,  # noqa: ARG002
        price: Decimal,
        quantity: Decimal,
    ) -> FillResult | None:
        """Fill at the exact requested price if capital is sufficient.

        Args:
            token_id: CLOB token identifier (unused).
            side: Order side (unused).
            price: Requested execution price.
            quantity: Requested token quantity.

        Returns:
            ``FillResult`` on success, ``None`` if capital is insufficient.

        """
        cost = price * quantity
        if cost > self._capital:
            return None
        self._capital -= cost
        return FillResult(price=price, quantity=quantity)

    def get_capital(self) -> Decimal:
        """Return available backtest capital."""
        return self._capital

    def total_capital(self) -> Decimal:
        """Return total backtest capital."""
        return self._total_capital

    def add_capital(self, amount: Decimal) -> None:
        """Add realised P&L back to available capital.

        Args:
            amount: USDC to add.

        """
        self._capital += amount
        self._total_capital += amount


class ReplayMarketData:
    """Market data adapter that serves pre-loaded snapshots.

    Feed historical data to the engine for backtesting.  Markets,
    order books, and candles are registered ahead of time, then
    served in response to queries.

    """

    def __init__(self) -> None:
        """Initialize with empty data stores."""
        self._markets: list[MarketOpportunity] = []
        self._order_books: dict[str, tuple[OrderBook, OrderBook]] = {}
        self._candles: dict[str, list[Candle]] = {}
        self._outcomes: dict[str, str] = {}
        self._whale_signals: dict[str, str] = {}
        self._ticks: dict[str, list[TickSample]] = {}

    def set_markets(self, markets: list[MarketOpportunity]) -> None:
        """Register markets to return from ``get_active_markets``.

        Args:
            markets: List of market opportunities to serve.

        """
        self._markets = markets

    def set_order_books(
        self,
        condition_id: str,
        up_book: OrderBook,
        down_book: OrderBook,
    ) -> None:
        """Register order books for a specific market.

        Args:
            condition_id: Market condition ID.
            up_book: Order book for the Up outcome.
            down_book: Order book for the Down outcome.

        """
        self._order_books[condition_id] = (up_book, down_book)

    def set_candles(self, asset: str, candles: list[Candle]) -> None:
        """Register candle data for an asset.

        Args:
            asset: Spot trading pair (e.g. ``"BTC-USD"``).
            candles: 1-min candles ordered oldest to newest.

        """
        self._candles[asset] = candles

    def set_outcome(self, condition_id: str, outcome: str) -> None:
        """Register the winning side for a market.

        Args:
            condition_id: Market condition ID.
            outcome: ``"Up"`` or ``"Down"``.

        """
        self._outcomes[condition_id] = outcome

    def set_whale_signal(self, condition_id: str, direction: str) -> None:
        """Register a whale directional signal for a market.

        Args:
            condition_id: Market condition ID.
            direction: ``"Up"`` or ``"Down"``.

        """
        self._whale_signals[condition_id] = direction

    def set_ticks(self, token_id: str, ticks: list[TickSample]) -> None:
        """Register tick samples for a token.

        Args:
            token_id: CLOB token identifier.
            ticks: Tick samples ordered by timestamp.

        """
        self._ticks[token_id] = ticks

    async def get_active_markets(
        self,
        open_cids: set[str],
    ) -> list[MarketOpportunity]:
        """Return pre-loaded markets, excluding already-open ones.

        Args:
            open_cids: Condition IDs to exclude.

        Returns:
            Filtered list of market opportunities.

        """
        return [m for m in self._markets if m.condition_id not in open_cids]

    async def get_order_books(
        self,
        up_token_id: str,
        down_token_id: str,  # noqa: ARG002
    ) -> tuple[OrderBook, OrderBook]:
        """Return pre-loaded order books for the given tokens.

        Args:
            up_token_id: Up token ID (used to look up by condition).
            down_token_id: Down token ID (unused — lookup by up_token).

        Returns:
            Tuple of ``(up_book, down_book)``.

        Raises:
            KeyError: If no order books registered for this market.

        """
        for mkt in self._markets:
            if mkt.up_token_id == up_token_id and mkt.condition_id in self._order_books:
                return self._order_books[mkt.condition_id]
        msg = f"No order books registered for up_token_id={up_token_id}"
        raise KeyError(msg)

    async def get_binance_candles(
        self,
        asset: str,
        start_ts: int,
        end_ts: int,
    ) -> Sequence[Candle]:
        """Return pre-loaded candles for the asset within the time range.

        Args:
            asset: Spot trading pair.
            start_ts: Start epoch seconds (inclusive).
            end_ts: End epoch seconds (inclusive).

        Returns:
            Filtered candle sequence.

        """
        all_candles = self._candles.get(asset, [])
        return [c for c in all_candles if start_ts <= c.timestamp <= end_ts]

    async def get_whale_signal(
        self,
        condition_id: str,
    ) -> str | None:
        """Return pre-loaded whale signal for a market.

        Args:
            condition_id: Market condition ID.

        Returns:
            ``"Up"`` or ``"Down"`` if registered, ``None`` otherwise.

        """
        return self._whale_signals.get(condition_id)

    async def get_recent_ticks(
        self,
        token_id: str,
        since_ms: int,
    ) -> list[TickSample]:
        """Return pre-loaded ticks for a token after a timestamp.

        Args:
            token_id: CLOB token identifier.
            since_ms: Epoch milliseconds — only return ticks after this.

        Returns:
            Filtered tick samples.

        """
        all_ticks = self._ticks.get(token_id, [])
        return [t for t in all_ticks if t.timestamp_ms >= since_ms]

    async def resolve_outcome(
        self,
        opportunity: MarketOpportunity,
    ) -> str | None:
        """Return pre-loaded outcome for the market.

        Args:
            opportunity: Market opportunity to resolve.

        Returns:
            ``"Up"`` or ``"Down"`` if registered, ``None`` otherwise.

        """
        return self._outcomes.get(opportunity.condition_id)
