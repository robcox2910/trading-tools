"""Live portfolio that executes real trades via the Polymarket CLOB API.

Wrap the authenticated ``PolymarketClient`` to place real BUY and SELL
orders, track open positions, and record ``LiveTrade`` objects.  Order
placement errors are caught and logged — a failed order returns ``None``
rather than raising, so the engine can continue operating.
"""

import logging
from decimal import Decimal

from trading_tools.apps.polymarket_bot.models import LiveTrade
from trading_tools.clients.polymarket.client import PolymarketClient
from trading_tools.clients.polymarket.exceptions import PolymarketAPIError
from trading_tools.clients.polymarket.models import OrderRequest
from trading_tools.core.models import ONE, ZERO, Position, Side

logger = logging.getLogger(__name__)


class LivePortfolio:
    """Execute real trades and track positions via the Polymarket CLOB.

    Manage opening and closing positions across multiple prediction markets,
    enforce per-market position size limits based on live USDC balance, and
    maintain a running trade log of ``LiveTrade`` records.

    Args:
        client: Authenticated Polymarket API client.
        max_position_pct: Maximum fraction of balance to allocate per market.
        use_market_orders: Use FOK market orders when ``True``, GTC limit
            orders when ``False``.

    """

    def __init__(
        self,
        client: PolymarketClient,
        max_position_pct: Decimal,
        *,
        use_market_orders: bool = True,
    ) -> None:
        """Initialize the live portfolio.

        Args:
            client: Authenticated Polymarket API client.
            max_position_pct: Maximum fraction of balance per market (0-1).
            use_market_orders: Use FOK market orders (default) or GTC limit.

        """
        self._client = client
        self._max_position_pct = max_position_pct
        self._use_market_orders = use_market_orders
        self._balance = ZERO
        self._positions: dict[str, Position] = {}
        self._mark_prices: dict[str, Decimal] = {}
        self._trades: list[LiveTrade] = []
        self._outcomes: dict[str, str] = {}
        self._token_ids: dict[str, str] = {}

    async def refresh_balance(self) -> Decimal:
        """Fetch the live USDC balance from the CLOB API.

        On transient API failures, log a warning and return the last known
        balance so the engine can continue operating.

        Returns:
            Current USDC balance as a ``Decimal``, or the last known balance
            if the API call fails.

        """
        try:
            bal = await self._client.get_balance("COLLATERAL")
            self._balance = bal.balance
        except Exception:
            logger.warning(
                "Balance refresh failed, using last known balance: $%.4f",
                self._balance,
                exc_info=True,
            )
        return self._balance

    async def open_position(
        self,
        condition_id: str,
        token_id: str,
        outcome: str,
        side: Side,
        price: Decimal,
        quantity: Decimal,
        timestamp: int,
        reason: str,
        edge: Decimal,
    ) -> LiveTrade | None:
        """Place a real BUY order on the CLOB and record the position.

        Reject duplicate positions or orders exceeding the per-market
        allocation limit. On API failure, log the error and return ``None``.

        Args:
            condition_id: Market condition identifier.
            token_id: CLOB token identifier for the outcome.
            outcome: Outcome token label ("Yes" or "No").
            side: Trade direction (BUY or SELL).
            price: Order price between 0 and 1.
            quantity: Number of tokens to trade.
            timestamp: Unix epoch seconds of execution.
            reason: Strategy's explanation for the trade.
            edge: Estimated probability edge over market price.

        Returns:
            A ``LiveTrade`` if the order was placed, or ``None`` if rejected
            or if the API call failed.

        """
        if condition_id in self._positions:
            logger.warning(
                "Rejected %s: duplicate position already open",
                condition_id[:20],
            )
            return None

        cost = price * quantity
        max_allocation = self._balance * self._max_position_pct
        if cost > max_allocation or cost > self._balance:
            logger.warning(
                "Rejected %s: cost=$%.4f exceeds max_alloc=$%.4f or balance=$%.4f",
                condition_id[:20],
                cost,
                max_allocation,
                self._balance,
            )
            return None

        order_type = "market" if self._use_market_orders else "limit"
        request = OrderRequest(
            token_id=token_id,
            side=side.value,
            price=price,
            size=quantity,
            order_type=order_type,
        )

        try:
            response = await self._client.place_order(request)
        except PolymarketAPIError as exc:
            logger.exception(
                "API error placing %s %s order for %s: %s (status=%s)",
                order_type,
                side.value,
                condition_id[:20],
                exc.msg,
                exc.status_code,
            )
            return None

        self._positions[condition_id] = Position(
            symbol=condition_id,
            side=side,
            quantity=quantity,
            entry_price=price,
            entry_time=timestamp,
        )
        self._mark_prices[condition_id] = price
        self._outcomes[condition_id] = outcome
        self._token_ids[condition_id] = token_id

        trade = LiveTrade(
            condition_id=condition_id,
            token_id=token_id,
            token_outcome=outcome,
            order_id=response.order_id,
            side=side,
            quantity=quantity,
            price=price,
            filled=response.filled,
            timestamp=timestamp,
            reason=reason,
            estimated_edge=edge,
        )
        self._trades.append(trade)
        return trade

    async def close_position(
        self,
        condition_id: str,
        token_id: str,
        price: Decimal,
        quantity: Decimal,
        timestamp: int,
    ) -> LiveTrade | None:
        """Place a real SELL order to exit an open position.

        Remove the position from tracking on success. On API failure,
        log the error and return ``None``.

        Args:
            condition_id: Market condition identifier.
            token_id: CLOB token identifier for the outcome.
            price: Exit price between 0 and 1.
            quantity: Number of tokens to sell.
            timestamp: Unix epoch seconds of exit.

        Returns:
            A ``LiveTrade`` recording the close, or ``None`` if no position
            exists or the API call failed.

        """
        pos = self._positions.get(condition_id)
        if pos is None:
            return None

        exit_side = Side.SELL if pos.side == Side.BUY else Side.BUY
        order_type = "market" if self._use_market_orders else "limit"
        request = OrderRequest(
            token_id=token_id,
            side=exit_side.value,
            price=price,
            size=quantity,
            order_type=order_type,
        )

        try:
            response = await self._client.place_order(request)
        except PolymarketAPIError as exc:
            logger.exception(
                "API error closing position for %s: %s (status=%s)",
                condition_id[:20],
                exc.msg,
                exc.status_code,
            )
            return None

        outcome = self._outcomes.pop(condition_id, "Yes")
        self._token_ids.pop(condition_id, None)
        del self._positions[condition_id]
        self._mark_prices.pop(condition_id, None)

        trade = LiveTrade(
            condition_id=condition_id,
            token_id=token_id,
            token_outcome=outcome,
            order_id=response.order_id,
            side=exit_side,
            quantity=quantity,
            price=price,
            filled=response.filled,
            timestamp=timestamp,
            reason="close_position",
            estimated_edge=ZERO,
        )
        self._trades.append(trade)
        return trade

    def clear_positions(self) -> None:
        """Remove all local position tracking.

        Use when markets have resolved and Polymarket has auto-redeemed
        winning tokens.  Does not place any orders — just clears internal
        state so the engine can start fresh for the next market window.
        """
        self._positions.clear()
        self._mark_prices.clear()
        self._outcomes.clear()
        self._token_ids.clear()

    def mark_to_market(self, condition_id: str, current_price: Decimal) -> None:
        """Update the mark-to-market price for an open position.

        Args:
            condition_id: Market condition identifier.
            current_price: Latest token price.

        """
        if condition_id in self._positions:
            self._mark_prices[condition_id] = current_price

    def max_quantity_for(self, price: Decimal) -> Decimal:
        """Return the maximum quantity affordable at the given price.

        Respect the per-market allocation limit and available balance.

        Args:
            price: Token price to compute quantity for.

        Returns:
            Maximum number of tokens that can be purchased.

        """
        if price <= ZERO:
            return ZERO
        max_allocation = self._balance * self._max_position_pct
        budget = min(max_allocation, self._balance)
        return (budget / price).quantize(ONE)

    @property
    def balance(self) -> Decimal:
        """Return the last-fetched USDC balance."""
        return self._balance

    @property
    def total_equity(self) -> Decimal:
        """Return total equity: balance plus mark-to-market value of positions."""
        unrealised = ZERO
        for cid, pos in self._positions.items():
            mark_price = self._mark_prices.get(cid, pos.entry_price)
            if pos.side == Side.BUY:
                unrealised += (mark_price - pos.entry_price) * pos.quantity
            else:
                unrealised += (pos.entry_price - mark_price) * pos.quantity
        return (
            self._balance
            + unrealised
            + sum(pos.entry_price * pos.quantity for pos in self._positions.values())
        )

    @property
    def positions(self) -> dict[str, Position]:
        """Return a copy of all open positions keyed by condition_id."""
        return dict(self._positions)

    @property
    def trades(self) -> list[LiveTrade]:
        """Return all recorded live trades."""
        return list(self._trades)

    def get_token_id(self, condition_id: str) -> str | None:
        """Return the token ID for an open position.

        Args:
            condition_id: Market condition identifier.

        Returns:
            Token ID string or ``None`` if no position exists.

        """
        return self._token_ids.get(condition_id)
