"""Portfolio state tracking for backtesting.

Manage capital allocation, open positions, and the running list of
completed trades during a backtest. The ``Portfolio`` enforces a
simple one-position-at-a-time rule: a BUY opens a position using all
available capital, and a SELL closes it.
"""

from decimal import Decimal

from trading_tools.core.models import Position, Side, Signal, Trade


class Portfolio:
    """Track capital, positions, and completed trades during a backtest.

    Hold at most one open position at a time. BUY signals open a
    position using all available capital; SELL signals close it,
    converting the position back into capital at the current price.
    """

    def __init__(self, initial_capital: Decimal) -> None:
        """Initialize the portfolio with the given starting capital.

        Args:
            initial_capital: Starting amount in quote currency.

        """
        self._capital = initial_capital
        self._position: Position | None = None
        self._trades: list[Trade] = []

    @property
    def capital(self) -> Decimal:
        """Return current available capital."""
        return self._capital

    @property
    def position(self) -> Position | None:
        """Return the current open position, if any."""
        return self._position

    @property
    def trades(self) -> list[Trade]:
        """Return a copy of completed trades."""
        return list(self._trades)

    def process_signal(self, signal: Signal, price: Decimal, timestamp: int) -> Trade | None:
        """Process a trading signal at the given price and time.

        Open a new position on BUY (if none is open) or close the
        existing position on SELL. Duplicate BUY or SELL signals are
        silently ignored.

        Args:
            signal: The trading signal to act on.
            price: Current market price.
            timestamp: Unix timestamp of the candle.

        Returns:
            A ``Trade`` if a position was closed, ``None`` otherwise.

        """
        if signal.side == Side.BUY and self._position is None:
            self._open_position(signal, price, timestamp)
            return None
        if signal.side == Side.SELL and self._position is not None:
            return self._close_position(price, timestamp)
        return None

    def force_close(self, price: Decimal, timestamp: int) -> Trade | None:
        """Force-close any open position at the end of a backtest.

        Args:
            price: Current market price to exit at.
            timestamp: Unix timestamp of the final candle.

        Returns:
            A ``Trade`` if a position was closed, ``None`` if no position was open.

        """
        if self._position is None:
            return None
        return self._close_position(price, timestamp)

    def _open_position(self, signal: Signal, price: Decimal, timestamp: int) -> None:
        """Open a new position using all available capital."""
        quantity = self._capital / price
        self._position = Position(
            symbol=signal.symbol,
            side=signal.side,
            quantity=quantity,
            entry_price=price,
            entry_time=timestamp,
        )
        self._capital = Decimal(0)

    def _close_position(self, price: Decimal, timestamp: int) -> Trade:
        """Close the current position and return the resulting trade."""
        if self._position is None:
            msg = "Cannot close position: no open position exists"
            raise RuntimeError(msg)
        trade = self._position.close(exit_price=price, exit_time=timestamp)
        self._capital = trade.quantity * price
        self._position = None
        self._trades.append(trade)
        return trade
