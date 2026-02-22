"""Performance metrics for evaluating backtest results.

Provide standalone functions that each compute a single metric from a
list of ``Trade`` objects. The ``calculate_metrics`` convenience function
runs all of them and returns a dictionary suitable for display.
"""

from decimal import Decimal

from trading_tools.core.models import Trade

ZERO = Decimal(0)
_MIN_TRADES_FOR_SHARPE = 2


def total_return(initial_capital: Decimal, final_capital: Decimal) -> Decimal:
    """Return the total portfolio return as a decimal fraction (e.g. 0.25 = +25%)."""
    if initial_capital == ZERO:
        return ZERO
    return (final_capital - initial_capital) / initial_capital


def win_rate(trades: list[Trade]) -> Decimal:
    """Return the fraction of trades with positive PnL (0.0 to 1.0)."""
    if not trades:
        return ZERO
    winners = sum(1 for t in trades if t.pnl > ZERO)
    return Decimal(winners) / Decimal(len(trades))


def profit_factor(trades: list[Trade]) -> Decimal:
    """Return gross profit divided by gross loss.

    A value above 1.0 means winning trades outweigh losers. Return
    ``Infinity`` when there are no losing trades, or zero when there
    are no trades at all.
    """
    gross_profit = sum((t.pnl for t in trades if t.pnl > ZERO), ZERO)
    gross_loss = abs(sum((t.pnl for t in trades if t.pnl < ZERO), ZERO))
    if gross_loss == ZERO:
        return Decimal("Infinity") if gross_profit > ZERO else ZERO
    return gross_profit / gross_loss


def max_drawdown(trades: list[Trade], initial_capital: Decimal) -> Decimal:
    """Return the maximum peak-to-trough equity decline as a fraction of the peak.

    Walk the equity curve trade-by-trade, tracking the running high-water
    mark and the largest percentage drop from that peak.
    """
    if not trades:
        return ZERO
    equity = initial_capital
    peak = equity
    max_dd = ZERO
    for trade in trades:
        equity += trade.pnl
        peak = max(peak, equity)
        dd = (peak - equity) / peak
        max_dd = max(max_dd, dd)
    return max_dd


def sharpe_ratio(trades: list[Trade]) -> Decimal:
    """Return a simplified Sharpe ratio (mean return / std dev of returns).

    Use a risk-free rate of zero for simplicity. Return zero when there
    are fewer than 2 trades or the standard deviation is zero.
    """
    if len(trades) < _MIN_TRADES_FOR_SHARPE:
        return ZERO
    returns = [t.pnl_pct for t in trades]
    mean = sum(returns) / Decimal(len(returns))
    variance = sum((r - mean) ** 2 for r in returns) / Decimal(len(returns) - 1)
    if variance == ZERO:
        return ZERO
    std_dev = variance.sqrt()
    return mean / std_dev


def total_fees(trades: list[Trade]) -> Decimal:
    """Return the sum of all entry and exit fees across all trades."""
    return sum((t.entry_fee + t.exit_fee for t in trades), ZERO)


def calculate_metrics(
    trades: list[Trade], initial_capital: Decimal, final_capital: Decimal
) -> dict[str, Decimal]:
    """Calculate all performance metrics and return them as a named dictionary."""
    return {
        "total_return": total_return(initial_capital, final_capital),
        "win_rate": win_rate(trades),
        "profit_factor": profit_factor(trades),
        "max_drawdown": max_drawdown(trades, initial_capital),
        "sharpe_ratio": sharpe_ratio(trades),
        "total_trades": Decimal(len(trades)),
        "total_fees": total_fees(trades),
    }
