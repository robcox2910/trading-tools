"""Abstract base portfolio for shared position tracking logic.

Factor out the position state management, mark-to-market accounting, and
Kelly-based quantity calculation that are identical between
``PaperPortfolio`` and ``LivePortfolio``.  Subclasses implement
``_get_cash_balance()`` to supply their specific cash/balance source.
"""

from abc import ABC, abstractmethod
from decimal import Decimal

from trading_tools.core.models import ONE, ZERO, Position, Side


class BasePortfolio(ABC):
    """Shared portfolio logic for paper and live prediction market trading.

    Track open positions, outcomes, and mark-to-market prices across
    multiple markets.  Enforce per-market allocation limits and compute
    total equity.  Concrete subclasses provide the cash balance via
    ``_get_cash_balance()``.

    Args:
        max_position_pct: Maximum fraction of cash to allocate per market.

    """

    def __init__(self, max_position_pct: Decimal) -> None:
        """Initialize shared portfolio state.

        Args:
            max_position_pct: Maximum fraction of cash per market (0-1).

        """
        self._max_position_pct = max_position_pct
        self._positions: dict[str, Position] = {}
        self._mark_prices: dict[str, Decimal] = {}
        self._outcomes: dict[str, str] = {}

    @abstractmethod
    def _get_cash_balance(self) -> Decimal:
        """Return the current cash balance for allocation calculations."""

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

        Respect the per-market allocation limit and available cash.

        Args:
            price: Token price to compute quantity for.

        Returns:
            Maximum number of tokens that can be purchased.

        """
        if price <= ZERO:
            return ZERO
        cash = self._get_cash_balance()
        max_allocation = cash * self._max_position_pct
        budget = min(max_allocation, cash)
        return (budget / price).quantize(ONE)

    @property
    def total_equity(self) -> Decimal:
        """Return total equity: cash plus mark-to-market value of all positions."""
        unrealised = ZERO
        for cid, pos in self._positions.items():
            mark_price = self._mark_prices.get(cid, pos.entry_price)
            if pos.side == Side.BUY:
                unrealised += (mark_price - pos.entry_price) * pos.quantity
            else:
                unrealised += (pos.entry_price - mark_price) * pos.quantity
        return (
            self._get_cash_balance()
            + unrealised
            + sum(pos.entry_price * pos.quantity for pos in self._positions.values())
        )

    @property
    def positions(self) -> dict[str, Position]:
        """Return a copy of all open positions keyed by condition_id."""
        return dict(self._positions)
