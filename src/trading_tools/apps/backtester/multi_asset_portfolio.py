"""Multi-asset portfolio tracking for backtesting.

Extend the single-asset portfolio concept to support multiple
simultaneous positions across different symbols. Capital is allocated
from a shared pool, so opening a position in one asset reduces the
capital available for others.
"""

from decimal import Decimal

from trading_tools.apps.backtester.execution import (
    apply_entry_slippage,
    apply_exit_slippage,
    compute_allocation,
)
from trading_tools.apps.backtester.portfolio import check_circuit_breaker
from trading_tools.core.models import (
    ZERO,
    Candle,
    ExecutionConfig,
    Position,
    RiskConfig,
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
    being unfairly sized by earlier wins/losses. Optionally halt
    trading when portfolio drawdown exceeds a circuit breaker threshold.
    """

    def __init__(
        self,
        initial_capital: Decimal,
        execution_config: ExecutionConfig | None = None,
        risk_config: RiskConfig | None = None,
    ) -> None:
        """Initialize the multi-asset portfolio.

        Args:
            initial_capital: Starting capital in quote currency.
            execution_config: Optional execution cost configuration.
            risk_config: Optional risk configuration for circuit breaker.

        """
        self._initial_capital = initial_capital
        self._capital = initial_capital
        self._positions: dict[str, Position] = {}
        self._trades: list[Trade] = []
        self._exec = execution_config or ExecutionConfig()
        self._risk = risk_config or RiskConfig()
        self._entry_fees: dict[str, Decimal] = {}
        self._halted = False
        self._peak_equity = initial_capital
        self._halt_equity = ZERO

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

    @property
    def halted(self) -> bool:
        """Return whether the circuit breaker has halted trading."""
        return self._halted

    def update_equity(self, prices: dict[str, Decimal]) -> None:
        """Update circuit breaker state based on current mark-to-market equity.

        Compute total equity as available capital plus the mark-to-market
        value of all open positions, then check the circuit breaker.

        Args:
            prices: Mapping of symbol to current market price.

        """
        equity = self._capital
        for symbol, pos in self._positions.items():
            if symbol in prices:
                equity += pos.quantity * prices[symbol]
        self._halted, self._peak_equity, self._halt_equity = check_circuit_breaker(
            halted=self._halted,
            equity=equity,
            peak_equity=self._peak_equity,
            halt_equity=self._halt_equity,
            circuit_breaker_pct=self._risk.circuit_breaker_pct,
            recovery_pct=self._risk.recovery_pct,
        )

    def process_signal(
        self,
        signal: Signal,
        price: Decimal,
        timestamp: int,
        history: list[Candle] | None = None,
    ) -> Trade | None:
        """Process a trading signal for a specific symbol.

        Open a position on BUY if the symbol has no open position and
        sufficient capital is available. Close on SELL if the symbol
        has an open position. Duplicate signals are silently ignored.
        When the circuit breaker is active, BUY signals are skipped.

        Args:
            signal: The trading signal to act on.
            price: Current market price for the signal's symbol.
            timestamp: Unix timestamp of the candle.
            history: Optional candle history for volatility-based sizing.

        Returns:
            A ``Trade`` if a position was closed, ``None`` otherwise.

        """
        symbol = signal.symbol
        if signal.side == Side.BUY and symbol not in self._positions:
            if self._halted:
                return None
            return self._open_position(signal, price, timestamp, history)
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

    def _open_position(
        self,
        signal: Signal,
        price: Decimal,
        timestamp: int,
        history: list[Candle] | None = None,
    ) -> None:
        """Open a position for the given symbol with fees and sizing.

        Delegate slippage and allocation calculations to shared execution
        helpers. Size against initial capital so later positions are not
        unfairly affected by earlier wins/losses.
        """
        effective_price = apply_entry_slippage(price, self._exec.slippage_pct)
        allocation, entry_fee, quantity = compute_allocation(
            capital=self._initial_capital,
            price=effective_price,
            exec_config=self._exec,
            history=history,
        )

        if allocation > self._capital:
            return

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
        effective_price = apply_exit_slippage(price, self._exec.slippage_pct)
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
