"""Portfolio state tracking for backtesting."""

from decimal import Decimal

from trading_tools.core.models import Position, Side, Signal, Trade


class Portfolio:
    """Tracks capital, positions, and completed trades during a backtest."""

    def __init__(self, initial_capital: Decimal) -> None:
        self._capital = initial_capital
        self._position: Position | None = None
        self._trades: list[Trade] = []

    @property
    def capital(self) -> Decimal:
        return self._capital

    @property
    def position(self) -> Position | None:
        return self._position

    @property
    def trades(self) -> list[Trade]:
        return list(self._trades)

    def process_signal(self, signal: Signal, price: Decimal, timestamp: int) -> Trade | None:
        """Process a signal at the given price and time.

        Returns a Trade if a position was closed, None otherwise.
        """
        if signal.side == Side.BUY and self._position is None:
            self._open_position(signal, price, timestamp)
            return None
        if signal.side == Side.SELL and self._position is not None:
            return self._close_position(price, timestamp)
        return None

    def force_close(self, price: Decimal, timestamp: int) -> Trade | None:
        """Force-close any open position at end of backtest."""
        if self._position is None:
            return None
        return self._close_position(price, timestamp)

    def _open_position(self, signal: Signal, price: Decimal, timestamp: int) -> None:
        quantity = self._capital / price
        self._position = Position(
            symbol=signal.symbol,
            side=signal.side,
            quantity=quantity,
            entry_price=price,
            entry_time=timestamp,
        )
        self._capital = Decimal("0")
        return None

    def _close_position(self, price: Decimal, timestamp: int) -> Trade:
        assert self._position is not None
        trade = self._position.close(exit_price=price, exit_time=timestamp)
        self._capital = trade.quantity * price
        self._position = None
        self._trades.append(trade)
        return trade
