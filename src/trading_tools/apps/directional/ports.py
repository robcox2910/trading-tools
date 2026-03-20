"""Protocol interfaces (ports) for the directional trading engine.

Define the I/O boundaries that ``DirectionalEngine`` depends on.
Concrete adapters implement these protocols for live, paper, and
backtest modes, letting the same decision logic run unmodified
in every context.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Sequence
    from decimal import Decimal

    from trading_tools.clients.polymarket.models import OrderBook
    from trading_tools.core.models import Candle

    from .models import MarketOpportunity, TickSample


@dataclass(frozen=True)
class FillResult:
    """Outcome of an attempted fill on a directional position.

    Returned by ``ExecutionPort.execute_fill`` to communicate the actual
    execution price and quantity back to the engine.  ``None`` fields
    indicate partial information (e.g. no order ID in paper mode).

    Attributes:
        price: Actual execution price (may differ from requested due
            to slippage or book walking).
        quantity: Number of tokens actually filled.
        order_id: CLOB order identifier, or ``None`` for paper/backtest.

    """

    price: Decimal
    quantity: Decimal
    order_id: str | None = None


@runtime_checkable
class ExecutionPort(Protocol):
    """How fills happen — abstract away live, paper, and backtest execution."""

    async def execute_fill(
        self,
        token_id: str,
        side: str,
        price: Decimal,
        quantity: Decimal,
    ) -> FillResult | None:
        """Attempt to fill a buy order for the given token.

        Args:
            token_id: CLOB token identifier for the outcome.
            side: ``"BUY"`` (always buying in directional trading).
            price: Desired execution price.
            quantity: Desired token quantity.

        Returns:
            A ``FillResult`` on success, or ``None`` if the fill was
            rejected or could not be executed.

        """
        ...  # pragma: no cover

    def get_capital(self) -> Decimal:
        """Return the current capital available for new positions.

        Returns:
            Available capital in USDC.

        """
        ...  # pragma: no cover

    def total_capital(self) -> Decimal:
        """Return the total capital for exposure-limit denominators.

        Returns:
            Total capital in USDC (available + committed).

        """
        ...  # pragma: no cover


@runtime_checkable
class MarketDataPort(Protocol):
    """Where data comes from — abstract away live API and replay sources."""

    async def get_active_markets(
        self,
        open_cids: set[str],
    ) -> list[MarketOpportunity]:
        """Scan for active markets eligible for directional trading.

        Return all active Up/Down markets, excluding those already held.

        Args:
            open_cids: Condition IDs of currently open positions.

        Returns:
            List of actionable market opportunities.

        """
        ...  # pragma: no cover

    async def get_order_books(
        self,
        up_token_id: str,
        down_token_id: str,
    ) -> tuple[OrderBook, OrderBook]:
        """Fetch order books for both sides of a market.

        Args:
            up_token_id: CLOB token ID for the Up outcome.
            down_token_id: CLOB token ID for the Down outcome.

        Returns:
            Tuple of ``(up_book, down_book)``.

        """
        ...  # pragma: no cover

    async def get_binance_candles(
        self,
        asset: str,
        start_ts: int,
        end_ts: int,
    ) -> Sequence[Candle]:
        """Fetch Binance 1-min candles for feature extraction.

        Args:
            asset: Spot trading pair (e.g. ``"BTC-USD"``).
            start_ts: Start epoch seconds (inclusive).
            end_ts: End epoch seconds (inclusive).

        Returns:
            List of 1-min candles ordered oldest to newest.

        """
        ...  # pragma: no cover

    async def get_whale_signal(
        self,
        condition_id: str,
    ) -> float | None:
        """Return a continuous whale directional signal for a market.

        Args:
            condition_id: Polymarket market condition identifier.

        Returns:
            Signal in ``[-1, 1]`` based on whale dollar volume ratio,
            or ``None`` if no whale activity.

        """
        ...  # pragma: no cover

    async def get_recent_ticks(
        self,
        token_id: str,
        since_ms: int,
    ) -> list[TickSample]:
        """Fetch recent Polymarket tick samples for a token.

        Args:
            token_id: CLOB token identifier for the Up outcome.
            since_ms: Epoch milliseconds — only return ticks after this.

        Returns:
            List of ``TickSample`` ordered by timestamp, or empty list
            when tick data is unavailable.

        """
        ...  # pragma: no cover

    async def resolve_outcome(
        self,
        opportunity: MarketOpportunity,
    ) -> str | None:
        """Determine the winning side of a settled market.

        Args:
            opportunity: The market opportunity with window timestamps
                and asset metadata.

        Returns:
            ``"Up"`` or ``"Down"`` if resolved, ``None`` otherwise.

        """
        ...  # pragma: no cover
