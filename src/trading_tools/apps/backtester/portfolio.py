"""Portfolio state tracking for backtesting.

Manage capital allocation, open positions, and the running list of
completed trades during a backtest. The ``Portfolio`` enforces a
simple one-position-at-a-time rule: a BUY opens a position using
available capital (subject to ``ExecutionConfig`` sizing), and a
SELL closes it. Fees and slippage are applied when configured.
"""

from decimal import Decimal

from trading_tools.apps.backtester.execution import (
    apply_entry_slippage,
    apply_exit_slippage,
    compute_allocation,
)
from trading_tools.core.models import (
    ONE,
    TWO,
    ZERO,
    Candle,
    ExecutionConfig,
    Position,
    RiskConfig,
    Side,
    Signal,
    Trade,
)


def check_circuit_breaker(
    *,
    halted: bool,
    equity: Decimal,
    peak_equity: Decimal,
    halt_equity: Decimal,
    circuit_breaker_pct: Decimal | None,
    recovery_pct: Decimal | None,
) -> tuple[bool, Decimal, Decimal]:
    """Evaluate the drawdown circuit breaker and return updated state.

    Check whether current equity triggers a trading halt (drawdown from
    peak exceeds threshold) or resumes from a halt (equity recovers
    sufficiently from the halt point).

    Args:
        halted: Whether trading is currently halted.
        equity: Current total portfolio equity.
        peak_equity: Highest equity observed so far.
        halt_equity: Equity level when the halt was triggered.
        circuit_breaker_pct: Drawdown fraction that triggers a halt.
        recovery_pct: Recovery fraction from halt equity to resume.

    Returns:
        Tuple of (halted, peak_equity, halt_equity) with updated values.

    """
    if circuit_breaker_pct is None:
        return False, peak_equity, halt_equity

    peak_equity = max(peak_equity, equity)

    if not halted:
        drawdown = (peak_equity - equity) / peak_equity if peak_equity > ZERO else ZERO
        if drawdown >= circuit_breaker_pct:
            return True, peak_equity, equity
        return False, peak_equity, halt_equity

    if recovery_pct is not None and halt_equity > ZERO:
        recovery_target = halt_equity * (ONE + recovery_pct)
        if equity >= recovery_target:
            return False, equity, ZERO

    return True, peak_equity, halt_equity


class Portfolio:
    """Track capital, positions, and completed trades during a backtest.

    Hold at most one open position at a time. BUY signals open a
    position using available capital (scaled by ``position_size_pct``);
    SELL signals close it, converting the position back into capital
    at the current price minus any fees and slippage. Optionally halt
    trading when portfolio drawdown exceeds a circuit breaker threshold.
    """

    def __init__(
        self,
        initial_capital: Decimal,
        execution_config: ExecutionConfig | None = None,
        risk_config: RiskConfig | None = None,
    ) -> None:
        """Initialize the portfolio with the given starting capital.

        Args:
            initial_capital: Starting amount in quote currency.
            execution_config: Optional execution cost configuration.
                Defaults to zero-cost, full-deployment behavior.
            risk_config: Optional risk configuration for circuit breaker.

        """
        self._capital = initial_capital
        self._position: Position | None = None
        self._trades: list[Trade] = []
        self._exec = execution_config or ExecutionConfig()
        self._risk = risk_config or RiskConfig()
        self._entry_fee = ZERO
        self._halted = False
        self._peak_equity = initial_capital
        self._halt_equity = ZERO

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

    @property
    def halted(self) -> bool:
        """Return whether the circuit breaker has halted trading."""
        return self._halted

    def update_equity(self, mark_price: Decimal) -> None:
        """Update circuit breaker state based on current mark-to-market equity.

        Compute total equity as available capital plus the mark-to-market
        value of any open position, then check the circuit breaker.

        Args:
            mark_price: Current market price for marking the open position.

        """
        equity = self._capital
        if self._position is not None:
            if self._position.side == Side.SELL:
                equity += self._position.quantity * (TWO * self._position.entry_price - mark_price)
            else:
                equity += self._position.quantity * mark_price
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
        """Process a trading signal at the given price and time.

        Open a new position on BUY (if none is open) or close the
        existing position on SELL. Duplicate BUY or SELL signals are
        silently ignored. When the circuit breaker is active, BUY
        signals are skipped.

        Args:
            signal: The trading signal to act on.
            price: Current market price.
            timestamp: Unix timestamp of the candle.
            history: Optional candle history for volatility-based sizing.

        Returns:
            A ``Trade`` if a position was closed, ``None`` otherwise.

        """
        if signal.side == Side.BUY and self._position is None:
            if self._halted:
                return None
            self._open_position(signal, price, timestamp, history)
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

    def _open_position(
        self,
        signal: Signal,
        price: Decimal,
        timestamp: int,
        history: list[Candle] | None = None,
    ) -> None:
        """Open a new position with slippage, fees, and position sizing applied.

        Delegate slippage and allocation calculations to shared execution
        helpers. Cap the allocation at ``position_size_pct`` of capital.
        """
        effective_price = apply_entry_slippage(price, self._exec.slippage_pct)
        allocation, entry_fee, quantity = compute_allocation(
            capital=self._capital,
            price=effective_price,
            exec_config=self._exec,
            history=history,
        )

        self._position = Position(
            symbol=signal.symbol,
            side=signal.side,
            quantity=quantity,
            entry_price=effective_price,
            entry_time=timestamp,
        )
        self._entry_fee = entry_fee
        self._capital -= allocation

    def _close_position(self, price: Decimal, timestamp: int) -> Trade:
        """Close the current position with slippage and fees applied."""
        if self._position is None:
            msg = "Cannot close position: no open position exists"
            raise RuntimeError(msg)

        effective_price = apply_exit_slippage(price, self._exec.slippage_pct)
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
