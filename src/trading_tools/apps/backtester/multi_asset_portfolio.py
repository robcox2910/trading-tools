"""Multi-asset portfolio tracking for backtesting.

Extend the single-asset portfolio concept to support multiple
simultaneous positions across different symbols. Capital is allocated
from a shared pool, so opening a position in one asset reduces the
capital available for others.
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


class MultiAssetPortfolio:
    """Track capital and multiple simultaneous positions across symbols.

    Unlike the single-asset ``Portfolio``, this class allows one open
    position per symbol concurrently. Each position is sized as a
    fixed fraction (``position_size_pct``) of the initial capital,
    not the remaining capital. This prevents later positions from
    being unfairly sized by earlier wins/losses.
    """

    def __init__(
        self,
        initial_capital: Decimal,
        execution_config: ExecutionConfig | None = None,
    ) -> None:
        """Initialize the multi-asset portfolio.

        Args:
            initial_capital: Starting capital in quote currency.
            execution_config: Optional execution cost configuration.

        """
        self._initial_capital = initial_capital
        self._capital = initial_capital
        self._positions: dict[str, Position] = {}
        self._trades: list[Trade] = []
        self._exec = execution_config or ExecutionConfig()
        self._entry_fees: dict[str, Decimal] = {}

    @property
    def capital(self) -> Decimal:
        """Return current available capital."""
        return self._capital

    @property
    def positions(self) -> dict[str, Position]:
        """Return a copy of currently open positions keyed by symbol."""
        return dict(self._positions)

    @property
    def trades(self) -> list[Trade]:
        """Return a copy of completed trades."""
        return list(self._trades)

    def process_signal(self, signal: Signal, price: Decimal, timestamp: int) -> Trade | None:
        """Process a trading signal for a specific symbol.

        Open a position on BUY if the symbol has no open position and
        sufficient capital is available. Close on SELL if the symbol
        has an open position. Duplicate signals are silently ignored.

        Args:
            signal: The trading signal to act on.
            price: Current market price for the signal's symbol.
            timestamp: Unix timestamp of the candle.

        Returns:
            A ``Trade`` if a position was closed, ``None`` otherwise.

        """
        symbol = signal.symbol
        if signal.side == Side.BUY and symbol not in self._positions:
            return self._open_position(signal, price, timestamp)
        if signal.side == Side.SELL and symbol in self._positions:
            return self._close_position(symbol, price, timestamp)
        return None

    def force_close_all(self, prices: dict[str, Decimal], timestamp: int) -> list[Trade]:
        """Force-close all open positions at the given prices.

        Args:
            prices: Mapping of symbol to current price for each open position.
            timestamp: Unix timestamp of the final candle.

        Returns:
            A list of ``Trade`` objects for each closed position.

        """
        trades: list[Trade] = []
        for symbol in list(self._positions):
            if symbol in prices:
                trade = self._close_position(symbol, prices[symbol], timestamp)
                trades.append(trade)
        return trades

    def _open_position(self, signal: Signal, price: Decimal, timestamp: int) -> None:
        """Open a position for the given symbol with fees and sizing."""
        allocation = self._initial_capital * self._exec.position_size_pct
        if allocation > self._capital:
            return

        effective_price = price * (Decimal(1) + self._exec.slippage_pct)
        entry_fee = allocation * self._exec.taker_fee_pct
        investable = allocation - entry_fee
        quantity = investable / effective_price

        self._positions[signal.symbol] = Position(
            symbol=signal.symbol,
            side=signal.side,
            quantity=quantity,
            entry_price=effective_price,
            entry_time=timestamp,
        )
        self._entry_fees[signal.symbol] = entry_fee
        self._capital -= allocation

    def _close_position(self, symbol: str, price: Decimal, timestamp: int) -> Trade:
        """Close the position for the given symbol with fees applied."""
        position = self._positions[symbol]
        effective_price = price * (Decimal(1) - self._exec.slippage_pct)
        exit_value = position.quantity * effective_price
        exit_fee = exit_value * self._exec.maker_fee_pct

        entry_fee = self._entry_fees.pop(symbol, ZERO)
        trade = position.close(
            exit_price=effective_price,
            exit_time=timestamp,
            entry_fee=entry_fee,
            exit_fee=exit_fee,
        )

        self._capital += exit_value - exit_fee
        del self._positions[symbol]
        self._trades.append(trade)
        return trade
