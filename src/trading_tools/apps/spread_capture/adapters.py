"""Concrete adapters for the spread capture engine ports.

Provide five adapter implementations that satisfy ``ExecutionPort`` and
``MarketDataPort``:

Execution:
    - ``LiveExecution`` — wraps ``OrderExecutor`` + ``BalanceManager``.
    - ``PaperExecution`` — applies slippage, tracks virtual capital.
    - ``BacktestExecution`` — walks historical order books for fills.

Market data:
    - ``LiveMarketData`` — wraps ``MarketScanner`` + clients.
    - ``ReplayMarketData`` — serves pre-loaded books with controllable clock.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING

import httpx

from trading_tools.clients.binance.exceptions import BinanceError
from trading_tools.clients.polymarket.models import OrderBook, OrderLevel
from trading_tools.core.models import ONE, ZERO, Candle, Interval
from trading_tools.data.providers.binance import BinanceCandleProvider

from .ports import FillResult

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from trading_tools.apps.bot_framework.balance_manager import BalanceManager
    from trading_tools.apps.bot_framework.order_executor import OrderExecutor
    from trading_tools.apps.tick_collector.models import OrderBookSnapshot
    from trading_tools.clients.binance.client import BinanceClient
    from trading_tools.clients.polymarket.client import PolymarketClient

    from .market_scanner import MarketScanner
    from .models import SpreadOpportunity

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Execution adapters
# ------------------------------------------------------------------


@dataclass
class LiveExecution:
    """Execute fills via the Polymarket CLOB (real orders).

    Wrap ``OrderExecutor`` for order placement and ``BalanceManager``
    for capital queries.

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
            A ``FillResult`` on success, ``None`` if the order failed.

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
        committed = self.committed_capital_fn()
        return self.balance_manager.balance + committed


def _zero_factory() -> Decimal:
    """Return ``ZERO`` for use as a dataclass default_factory."""
    return ZERO


@dataclass
class PaperExecution:
    """Simulate fills with configurable slippage and virtual capital.

    Track a virtual capital balance that starts from a base amount
    and optionally compounds realised P&L.

    Attributes:
        base_capital: Starting paper capital in USDC.
        slippage_pct: Percentage of slippage applied to each fill.
        compound_profits: Whether to add realised P&L to base capital.
        committed_capital_fn: Callable that returns current committed capital.
        realised_pnl_fn: Callable that returns total realised P&L.

    """

    base_capital: Decimal
    slippage_pct: Decimal
    compound_profits: bool = True
    committed_capital_fn: Callable[[], Decimal] = field(default=_zero_factory)
    realised_pnl_fn: Callable[[], Decimal] = field(default=_zero_factory)

    async def execute_fill(
        self,
        token_id: str,  # noqa: ARG002
        side: str,  # noqa: ARG002
        price: Decimal,
        quantity: Decimal,
    ) -> FillResult | None:
        """Simulate a fill with slippage applied to the price.

        Args:
            token_id: CLOB token identifier (unused in paper mode).
            side: Trade direction (unused in paper mode).
            price: Desired execution price.
            quantity: Desired token quantity.

        Returns:
            A ``FillResult`` with slippage-adjusted price.

        """
        fill_price = price * (ONE + self.slippage_pct)
        return FillResult(price=fill_price, quantity=quantity)

    def get_capital(self) -> Decimal:
        """Return available paper capital after subtracting committed.

        Returns:
            Available capital in USDC.

        """
        base = self.base_capital
        if self.compound_profits:
            base += self.realised_pnl_fn()
        return base - self.committed_capital_fn()

    def total_capital(self) -> Decimal:
        """Return total paper capital (base + P&L if compounding).

        Returns:
            Total capital in USDC.

        """
        base = self.base_capital
        if self.compound_profits:
            base += self.realised_pnl_fn()
        return base


def _parse_book_snapshot(snapshot: OrderBookSnapshot) -> OrderBook:
    """Convert a stored ``OrderBookSnapshot`` into a typed ``OrderBook``.

    Deserialise the JSON bid/ask arrays and compute spread and midpoint
    from the best levels.

    Args:
        snapshot: The stored snapshot with JSON-serialised levels.

    Returns:
        A typed ``OrderBook`` instance.

    """
    bids_raw: list[list[str]] = json.loads(snapshot.bids_json)
    asks_raw: list[list[str]] = json.loads(snapshot.asks_json)

    bids = tuple(OrderLevel(price=Decimal(b[0]), size=Decimal(b[1])) for b in bids_raw)
    asks = tuple(OrderLevel(price=Decimal(a[0]), size=Decimal(a[1])) for a in asks_raw)

    best_bid = bids[0].price if bids else ZERO
    best_ask = asks[0].price if asks else ONE

    return OrderBook(
        token_id=snapshot.token_id,
        bids=bids,
        asks=asks,
        spread=best_ask - best_bid,
        midpoint=(best_ask + best_bid) / Decimal(2),
    )


@dataclass
class BacktestExecution:
    """Walk historical order books to simulate realistic fills.

    Consume ask levels in price-priority order and compute a VWAP
    fill price, mimicking the real market impact of each fill.

    Attributes:
        capital: Virtual starting capital for the backtest.
        slippage_pct: Additional slippage to model execution friction.
        committed_capital_fn: Callable that returns current committed capital.
        realised_pnl_fn: Callable that returns total realised P&L.

    """

    capital: Decimal
    slippage_pct: Decimal = ZERO
    committed_capital_fn: Callable[[], Decimal] = field(default=_zero_factory)
    realised_pnl_fn: Callable[[], Decimal] = field(default=_zero_factory)

    async def execute_fill(
        self,
        token_id: str,  # noqa: ARG002
        side: str,  # noqa: ARG002
        price: Decimal,
        quantity: Decimal,
    ) -> FillResult | None:
        """Simulate a fill with optional slippage.

        In backtest mode, fills always succeed at the requested price
        plus slippage (the order book depth check happens at the engine
        level via fill_qty clamping).

        Args:
            token_id: CLOB token identifier (unused).
            side: Trade direction (unused).
            price: Desired execution price.
            quantity: Desired token quantity.

        Returns:
            A ``FillResult`` with slippage-adjusted price.

        """
        fill_price = price * (ONE + self.slippage_pct)
        return FillResult(price=fill_price, quantity=quantity)

    def get_capital(self) -> Decimal:
        """Return available backtest capital.

        Returns:
            Available capital in USDC.

        """
        return self.capital - self.committed_capital_fn()

    def total_capital(self) -> Decimal:
        """Return total backtest capital (initial + realised P&L).

        Returns:
            Total capital in USDC.

        """
        return self.capital + self.realised_pnl_fn()


# ------------------------------------------------------------------
# Market data adapters
# ------------------------------------------------------------------


@dataclass
class LiveMarketData:
    """Fetch live market data from Polymarket and Binance APIs.

    Wrap the ``MarketScanner`` for opportunity discovery, the
    ``PolymarketClient`` for order books, and the ``BinanceClient``
    for candle data.

    Attributes:
        scanner: Market scanner for discovering spread opportunities.
        client: Polymarket client for CLOB order book queries.
        binance: Binance client for candle data.
        live: Whether to use Polymarket resolution for outcomes.

    """

    scanner: MarketScanner
    client: PolymarketClient
    binance: BinanceClient
    live: bool = False
    _whale_addresses: tuple[str, ...] = ()

    async def get_order_books(
        self,
        up_token_id: str,
        down_token_id: str,
    ) -> tuple[OrderBook, OrderBook]:
        """Fetch live order books for both sides from the CLOB.

        Args:
            up_token_id: CLOB token ID for the Up outcome.
            down_token_id: CLOB token ID for the Down outcome.

        Returns:
            Tuple of ``(up_book, down_book)``.

        """
        up_book, down_book = await asyncio.gather(
            self.client.get_order_book(up_token_id),
            self.client.get_order_book(down_token_id),
        )
        return up_book, down_book

    async def get_opportunities(
        self,
        open_cids: set[str],
    ) -> list[SpreadOpportunity]:
        """Scan for new spread opportunities via the market scanner.

        Args:
            open_cids: Condition IDs to exclude (already open).

        Returns:
            List of actionable spread opportunities.

        """
        return await self.scanner.scan_per_side(open_cids, ONE)

    async def get_binance_candles(
        self,
        asset: str,
        start_ts: int,
        end_ts: int,
    ) -> Sequence[Candle]:
        """Fetch 1-min Binance candles for the given asset and time range.

        Args:
            asset: Spot trading pair (e.g. ``"BTC-USD"``).
            start_ts: Start epoch seconds.
            end_ts: End epoch seconds.

        Returns:
            List of 1-min candles ordered oldest to newest.

        """
        provider = BinanceCandleProvider(self.binance)
        return await provider.get_candles(asset, Interval.M1, start_ts, end_ts)

    async def resolve_outcome(
        self,
        opportunity: SpreadOpportunity,
    ) -> str | None:
        """Determine outcome via Polymarket resolution or Binance candles.

        In live mode, first check Polymarket for redeemable positions.
        Fall back to Binance candle data (first vs last candle direction).

        Args:
            opportunity: The opportunity with window timestamps.

        Returns:
            ``"Up"`` or ``"Down"`` if resolved, ``None`` otherwise.

        """
        if self.live:
            try:
                positions = await self.client.get_redeemable_positions()
                for rp in positions:
                    if rp.condition_id == opportunity.condition_id:
                        return rp.outcome
            except (httpx.HTTPError, Exception):
                logger.debug("Failed to check redeemable positions, falling back to Binance")

        return await self._resolve_via_binance(opportunity)

    async def get_whale_signal(self, condition_id: str, since_ts: int) -> str | None:
        """Query the Polymarket Data API for whale trades on this market.

        Fetch recent trades for each tracked whale address, filter for
        BUY trades on the given condition_id, and return the outcome
        with the larger total dollar volume across all whales.

        Args:
            condition_id: Polymarket market condition identifier.
            since_ts: Unused (kept for protocol compatibility).

        Returns:
            ``"Up"`` or ``"Down"`` if whales have a clear directional
            bet, ``None`` if no whale addresses configured or no activity.

        """
        _ = since_ts
        if not self._whale_addresses:
            return None
        try:
            return await self._fetch_whale_signal_from_api(condition_id)
        except Exception:
            logger.debug("Failed to query whale signal for %s", condition_id[:12])
            return None

    async def _fetch_whale_signal_from_api(self, condition_id: str) -> str | None:
        """Hit the Polymarket Data API directly for real-time whale trades.

        Args:
            condition_id: Market condition identifier.

        Returns:
            ``"Up"`` or ``"Down"``, or ``None`` if no whale activity.

        """
        dollar_vol: dict[str, float] = {}

        async with httpx.AsyncClient(timeout=5.0) as http:
            for address in self._whale_addresses:
                resp = await http.get(
                    "https://data-api.polymarket.com/trades",
                    params={
                        "user": address,
                        "conditionId": condition_id,
                        "limit": 100,
                    },
                )
                if not resp.is_success:
                    continue
                for trade in resp.json():
                    if trade.get("side", "").upper() != "BUY":
                        continue
                    outcome = trade.get("outcome", "")
                    size = float(trade.get("size", 0))
                    price = float(trade.get("price", 0))
                    dollar_vol[outcome] = dollar_vol.get(outcome, 0.0) + size * price

        if not dollar_vol:
            return None

        top = max(dollar_vol, key=lambda k: dollar_vol[k])
        if top in ("Up", "Down"):
            return top
        return None

    async def _resolve_via_binance(self, opp: SpreadOpportunity) -> str | None:
        """Determine which side won via Binance spot price movement.

        Args:
            opp: The opportunity with window timestamps and asset.

        Returns:
            ``"Up"`` if price went up, ``"Down"`` if down, ``None``
            if candle data was unavailable.

        """
        try:
            provider = BinanceCandleProvider(self.binance)
            candles = await provider.get_candles(
                opp.asset, Interval.M1, opp.window_start_ts, opp.window_end_ts
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


@dataclass
class ReplayMarketData:
    """Serve pre-loaded historical data with a controllable clock.

    Feed stored order book snapshots and candle data to the engine
    during backtest replay.  The ``clock`` attribute controls which
    snapshots are visible at any given time.

    Attributes:
        opportunities: Pre-built list of opportunities for this replay.
        up_books: Mapping from timestamp (ms) to Up-side order book snapshots.
        down_books: Mapping from timestamp (ms) to Down-side order book snapshots.
        candles: Pre-loaded 1-min candles keyed by asset.
        outcome: Pre-resolved outcome for the market (``"Up"`` or ``"Down"``).
        clock: Current replay time in epoch seconds (advanced by runner).

    """

    opportunities: list[SpreadOpportunity]
    up_books: dict[int, OrderBookSnapshot]
    down_books: dict[int, OrderBookSnapshot]
    candles: dict[str, list[Candle]]
    outcome: str | None = None
    clock: int = 0
    _opportunities_served: bool = field(default=False, repr=False)

    async def get_order_books(
        self,
        up_token_id: str,  # noqa: ARG002
        down_token_id: str,  # noqa: ARG002
    ) -> tuple[OrderBook, OrderBook]:
        """Return the nearest order book snapshots for the current clock time.

        Args:
            up_token_id: Up token ID (used for matching, but we use
                the pre-loaded books directly).
            down_token_id: Down token ID (same as above).

        Returns:
            Tuple of ``(up_book, down_book)`` nearest to the clock time.

        """
        clock_ms = self.clock * 1000
        up_book = self._nearest_book(self.up_books, clock_ms)
        down_book = self._nearest_book(self.down_books, clock_ms)
        return up_book, down_book

    async def get_opportunities(
        self,
        open_cids: set[str],
    ) -> list[SpreadOpportunity]:
        """Return pre-built opportunities on the first call, then empty.

        Args:
            open_cids: Condition IDs to exclude (already open).

        Returns:
            List of opportunities not already open.

        """
        if self._opportunities_served:
            return []
        self._opportunities_served = True
        return [opp for opp in self.opportunities if opp.condition_id not in open_cids]

    async def get_binance_candles(
        self,
        asset: str,
        start_ts: int,
        end_ts: int,
    ) -> Sequence[Candle]:
        """Return pre-loaded candles for the asset within the time range.

        Args:
            asset: Spot trading pair.
            start_ts: Start epoch seconds.
            end_ts: End epoch seconds.

        Returns:
            Filtered list of candles within the range.

        """
        all_candles = self.candles.get(asset, [])
        return [c for c in all_candles if start_ts <= c.timestamp <= end_ts]

    async def resolve_outcome(
        self,
        opportunity: SpreadOpportunity,  # noqa: ARG002
    ) -> str | None:
        """Return the pre-resolved outcome.

        Args:
            opportunity: The spread opportunity (unused — outcome is
                pre-determined for backtest).

        Returns:
            ``"Up"`` or ``"Down"`` if known, ``None`` otherwise.

        """
        return self.outcome

    async def get_whale_signal(
        self,
        condition_id: str,  # noqa: ARG002
        since_ts: int,  # noqa: ARG002
    ) -> str | None:
        """Return ``None`` — no whale data available in backtest mode.

        Args:
            condition_id: Unused in backtest.
            since_ts: Unused in backtest.

        Returns:
            Always ``None``.

        """
        return None

    @staticmethod
    def _nearest_book(
        books: dict[int, OrderBookSnapshot],
        target_ms: int,
    ) -> OrderBook:
        """Find the snapshot nearest to the target timestamp.

        Args:
            books: Mapping from timestamp (ms) to snapshots.
            target_ms: Target epoch milliseconds.

        Returns:
            Parsed ``OrderBook`` from the nearest snapshot, or an
            empty book if no snapshots are available.

        """
        if not books:
            return OrderBook(
                token_id="",
                bids=(),
                asks=(),
                spread=ZERO,
                midpoint=ZERO,
            )

        nearest_ts = min(books.keys(), key=lambda ts: abs(ts - target_ms))
        return _parse_book_snapshot(books[nearest_ts])
