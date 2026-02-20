"""Portfolio state tracking for backtesting.

Manage capital allocation, open positions, and the running list of
completed trades during a backtest. The ``Portfolio`` enforces a
simple one-position-at-a-time rule: a BUY opens a position using
available capital (subject to ``ExecutionConfig`` sizing), and a
SELL closes it. Fees and slippage are applied when configured.
"""

from decimal import Decimal

from trading_tools.core.models import (
    ZERO,
    ExecutionConfig,
    Position,
    Side,
    Signal,
    Trade,
)


class Portfolio:
    """Track capital, positions, and completed trades during a backtest.

    Hold at most one open position at a time. BUY signals open a
    position using available capital (scaled by ``position_size_pct``);
    SELL signals close it, converting the position back into capital
    at the current price minus any fees and slippage.
    """

    def __init__(
        self,
        initial_capital: Decimal,
        execution_config: ExecutionConfig | None = None,
    ) -> None:
        """Initialize the portfolio with the given starting capital.

        Args:
            initial_capital: Starting amount in quote currency.
            execution_config: Optional execution cost configuration.
                Defaults to zero-cost, full-deployment behavior.

        """
        self._capital = initial_capital
        self._position: Position | None = None
        self._trades: list[Trade] = []
        self._exec = execution_config or ExecutionConfig()
        self._entry_fee = ZERO

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
        """Open a new position with slippage, fees, and position sizing applied."""
        effective_price = price * (Decimal(1) + self._exec.slippage_pct)
        available = self._capital * self._exec.position_size_pct
        entry_fee = available * self._exec.taker_fee_pct
        investable = available - entry_fee
        quantity = investable / effective_price

        self._position = Position(
            symbol=signal.symbol,
            side=signal.side,
            quantity=quantity,
            entry_price=effective_price,
            entry_time=timestamp,
        )
        self._entry_fee = entry_fee
        self._capital -= available

    def _close_position(self, price: Decimal, timestamp: int) -> Trade:
        """Close the current position with slippage and fees applied."""
        if self._position is None:
            msg = "Cannot close position: no open position exists"
            raise RuntimeError(msg)

        effective_price = price * (Decimal(1) - self._exec.slippage_pct)
        exit_value = self._position.quantity * effective_price
        exit_fee = exit_value * self._exec.maker_fee_pct

        trade = self._position.close(
            exit_price=effective_price,
            exit_time=timestamp,
            entry_fee=self._entry_fee,
            exit_fee=exit_fee,
        )

        self._capital += exit_value - exit_fee
        self._position = None
        self._entry_fee = ZERO
        self._trades.append(trade)
        return trade
