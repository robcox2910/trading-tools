"""Multi-position paper portfolio for prediction market trading.

Track multiple open positions across different markets, record virtual
trades, and compute mark-to-market equity. Unlike the backtester's
single-position portfolio, this supports simultaneous positions in
multiple prediction markets.
"""

from decimal import Decimal

from trading_tools.apps.polymarket_bot.base_portfolio import BasePortfolio
from trading_tools.apps.polymarket_bot.models import PaperTrade
from trading_tools.core.models import ZERO, Position, Side


class PaperPortfolio(BasePortfolio):
    """Track multiple virtual positions and capital for paper trading.

    Manage opening and closing positions across multiple prediction markets,
    enforce per-market position size limits, and maintain a running trade
    log. Each position is identified by its ``condition_id``.

    Args:
        initial_capital: Starting virtual capital in USD.
        max_position_pct: Maximum fraction of capital to allocate per market.

    """

    def __init__(self, initial_capital: Decimal, max_position_pct: Decimal) -> None:
        """Initialize the portfolio with starting capital and position limits.

        Args:
            initial_capital: Starting virtual capital in USD.
            max_position_pct: Maximum fraction of capital per market (0-1).

        """
        super().__init__(max_position_pct)
        self._cash = initial_capital
        self._initial_capital = initial_capital
        self._trades: list[PaperTrade] = []
        self._edges: dict[str, Decimal] = {}
        self._reasons: dict[str, str] = {}

    def _get_cash_balance(self) -> Decimal:
        """Return the current virtual cash balance."""
        return self._cash

    def open_position(
        self,
        condition_id: str,
        outcome: str,
        side: Side,
        price: Decimal,
        quantity: Decimal,
        timestamp: int,
        reason: str,
        edge: Decimal,
    ) -> PaperTrade | None:
        """Open a virtual position in a prediction market.

        Deduct the cost from available cash and record the trade. Refuse
        to open if a position already exists for this market or if the
        cost would exceed the per-market allocation limit.

        Args:
            condition_id: Market condition identifier.
            outcome: Outcome token ("Yes" or "No").
            side: Trade direction (BUY or SELL).
            price: Execution price between 0 and 1.
            quantity: Number of tokens to trade.
            timestamp: Unix epoch seconds of execution.
            reason: Strategy's explanation for the trade.
            edge: Estimated probability edge over market price.

        Returns:
            A ``PaperTrade`` if the position was opened, or ``None`` if
            rejected (duplicate position or insufficient capital).

        """
        if condition_id in self._positions:
            return None

        cost = price * quantity
        max_allocation = self._cash * self._max_position_pct
        if cost > max_allocation or cost > self._cash:
            return None

        self._cash -= cost
        self._positions[condition_id] = Position(
            symbol=condition_id,
            side=side,
            quantity=quantity,
            entry_price=price,
            entry_time=timestamp,
        )
        self._mark_prices[condition_id] = price
        self._outcomes[condition_id] = outcome
        self._edges[condition_id] = edge
        self._reasons[condition_id] = reason

        trade = PaperTrade(
            condition_id=condition_id,
            token_outcome=outcome,
            side=side,
            quantity=quantity,
            price=price,
            timestamp=timestamp,
            reason=reason,
            estimated_edge=edge,
        )
        self._trades.append(trade)
        return trade

    def close_position(
        self,
        condition_id: str,
        price: Decimal,
        timestamp: int,
    ) -> PaperTrade | None:
        """Close a virtual position and return cash proceeds.

        Compute PnL based on entry and exit prices, credit the proceeds
        back to cash, and remove the position from tracking.

        Args:
            condition_id: Market condition identifier.
            price: Exit price between 0 and 1.
            timestamp: Unix epoch seconds of exit.

        Returns:
            A ``PaperTrade`` recording the close, or ``None`` if no
            position exists for this market.

        """
        pos = self._positions.get(condition_id)
        if pos is None:
            return None

        # Proceeds = market value of the tokens at exit price, regardless
        # of which side opened the position.
        proceeds = price * pos.quantity
        self._cash += proceeds

        exit_side = Side.SELL if pos.side == Side.BUY else Side.BUY
        outcome = self._outcomes.pop(condition_id, "Yes")
        edge = self._edges.pop(condition_id, ZERO)
        self._reasons.pop(condition_id, "")
        del self._positions[condition_id]
        self._mark_prices.pop(condition_id, None)

        trade = PaperTrade(
            condition_id=condition_id,
            token_outcome=outcome,
            side=exit_side,
            quantity=pos.quantity,
            price=price,
            timestamp=timestamp,
            reason="close_position",
            estimated_edge=edge,
        )
        self._trades.append(trade)
        return trade

    @property
    def capital(self) -> Decimal:
        """Return the current cash balance (excluding unrealised gains)."""
        return self._cash

    @property
    def trades(self) -> list[PaperTrade]:
        """Return all recorded paper trades."""
        return list(self._trades)
