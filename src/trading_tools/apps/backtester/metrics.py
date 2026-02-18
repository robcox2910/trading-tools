"""Performance metrics for backtest results."""

from decimal import Decimal

from trading_tools.core.models import Trade

ZERO = Decimal("0")


def total_return(initial_capital: Decimal, final_capital: Decimal) -> Decimal:
    """Total return as a percentage."""
    return (final_capital - initial_capital) / initial_capital


def win_rate(trades: list[Trade]) -> Decimal:
    """Fraction of trades that were profitable."""
    if not trades:
        return ZERO
    winners = sum(1 for t in trades if t.pnl > ZERO)
    return Decimal(winners) / Decimal(len(trades))


def profit_factor(trades: list[Trade]) -> Decimal:
    """Gross profit divided by gross loss. Returns 0 if no losing trades."""
    gross_profit = sum((t.pnl for t in trades if t.pnl > ZERO), ZERO)
    gross_loss = abs(sum((t.pnl for t in trades if t.pnl < ZERO), ZERO))
    if gross_loss == ZERO:
        return ZERO
    return gross_profit / gross_loss


def max_drawdown(trades: list[Trade], initial_capital: Decimal) -> Decimal:
    """Maximum peak-to-trough decline as a percentage of peak."""
    if not trades:
        return ZERO
    equity = initial_capital
    peak = equity
    max_dd = ZERO
    for trade in trades:
        equity += trade.pnl
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak
        if dd > max_dd:
            max_dd = dd
    return max_dd


def sharpe_ratio(trades: list[Trade]) -> Decimal:
    """Simplified Sharpe ratio (mean return / std dev of returns).

    Uses risk-free rate of 0 for simplicity. Returns 0 if fewer than 2 trades
    or zero standard deviation.
    """
    if len(trades) < 2:
        return ZERO
    returns = [t.pnl_pct for t in trades]
    mean = sum(returns) / Decimal(len(returns))
    variance = sum((r - mean) ** 2 for r in returns) / Decimal(len(returns))
    if variance == ZERO:
        return ZERO
    std_dev = variance.sqrt()
    return mean / std_dev


def calculate_metrics(
    trades: list[Trade], initial_capital: Decimal, final_capital: Decimal
) -> dict[str, Decimal]:
    """Calculate all metrics and return as a dictionary."""
    return {
        "total_return": total_return(initial_capital, final_capital),
        "win_rate": win_rate(trades),
        "profit_factor": profit_factor(trades),
        "max_drawdown": max_drawdown(trades, initial_capital),
        "sharpe_ratio": sharpe_ratio(trades),
        "total_trades": Decimal(len(trades)),
    }
