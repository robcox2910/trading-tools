"""Monte Carlo simulation for backtest results.

Shuffle the order of trades from a completed backtest to generate
a distribution of possible equity outcomes. This reveals how
sensitive the backtest metrics are to the sequence of trades, helping
distinguish skill from luck.

The core idea: if a strategy's edge depends on the exact order trades
occurred, it may not be robust. By reshuffling trade order many times
and recomputing metrics, we get confidence intervals for total return,
max drawdown, and Sharpe ratio.
"""

import random
from dataclasses import dataclass
from decimal import Decimal

from trading_tools.apps.backtester.metrics import (
    max_drawdown,
    sharpe_ratio,
    total_return,
)
from trading_tools.core.models import BacktestResult, Trade

_MIN_TRADES = 2
_PERCENTILE_INDICES = (5, 25, 50, 75, 95)
_METRIC_NAMES = ("total_return", "max_drawdown", "sharpe_ratio")


@dataclass(frozen=True)
class MonteCarloDistribution:
    """Percentile distribution of a single metric across all shuffles.

    Capture the mean, standard deviation, and key percentiles (5th,
    25th, 50th, 75th, 95th) for one metric computed across many
    reshuffled trade sequences.
    """

    metric_name: str
    mean: float
    std: float
    percentile_5: float
    percentile_25: float
    percentile_50: float
    percentile_75: float
    percentile_95: float


@dataclass(frozen=True)
class MonteCarloResult:
    """Aggregate result of a Monte Carlo simulation.

    Bundle the number of shuffles performed, the original backtest
    result, and the computed percentile distributions for each metric
    (total return, max drawdown, Sharpe ratio).
    """

    num_shuffles: int
    original: BacktestResult
    distributions: tuple[MonteCarloDistribution, ...]


def run_monte_carlo(
    result: BacktestResult,
    num_shuffles: int = 1000,
    seed: int | None = None,
) -> MonteCarloResult:
    """Run a Monte Carlo simulation by reshuffling trade order.

    For each shuffle, reconstruct the equity curve from the initial
    capital and cumulative PnL of the reshuffled trades. Compute total
    return, max drawdown, and Sharpe ratio for every permutation, then
    summarise each metric as a percentile distribution.

    Args:
        result: A completed backtest result with at least 2 trades.
        num_shuffles: Number of random permutations to generate.
        seed: Optional RNG seed for reproducibility.

    Returns:
        A ``MonteCarloResult`` with percentile distributions for each metric.

    Raises:
        ValueError: If the result contains fewer than 2 trades.

    """
    if len(result.trades) < _MIN_TRADES:
        msg = f"Monte Carlo requires at least {_MIN_TRADES} trades, got {len(result.trades)}"
        raise ValueError(msg)

    rng = random.Random(seed)  # noqa: S311
    trades = list(result.trades)
    initial = result.initial_capital

    returns: list[float] = []
    drawdowns: list[float] = []
    sharpes: list[float] = []

    for _ in range(num_shuffles):
        shuffled = rng.sample(trades, len(trades))
        final = _final_capital(shuffled, initial)
        ret = total_return(initial, final)
        dd = max_drawdown(shuffled, initial)
        sr = sharpe_ratio(shuffled)
        returns.append(float(ret))
        drawdowns.append(float(dd))
        sharpes.append(float(sr))

    distributions = (
        _build_distribution("total_return", returns),
        _build_distribution("max_drawdown", drawdowns),
        _build_distribution("sharpe_ratio", sharpes),
    )

    return MonteCarloResult(
        num_shuffles=num_shuffles,
        original=result,
        distributions=distributions,
    )


def _final_capital(trades: list[Trade], initial_capital: Decimal) -> Decimal:
    """Compute final capital from initial capital and trade PnLs."""
    return initial_capital + sum((t.pnl for t in trades), Decimal(0))


def _percentile(sorted_values: list[float], pct: int) -> float:
    """Compute the p-th percentile from a pre-sorted list using nearest-rank.

    Args:
        sorted_values: Values sorted in ascending order.
        pct: Percentile to compute (0-100).

    Returns:
        The value at the given percentile.

    """
    idx = max(0, min(len(sorted_values) - 1, int(len(sorted_values) * pct / 100)))
    return sorted_values[idx]


def _build_distribution(name: str, values: list[float]) -> MonteCarloDistribution:
    """Compute mean, std, and percentiles for a list of metric values."""
    n = len(values)
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / n
    std = variance**0.5

    sorted_vals = sorted(values)
    return MonteCarloDistribution(
        metric_name=name,
        mean=mean,
        std=std,
        percentile_5=_percentile(sorted_vals, 5),
        percentile_25=_percentile(sorted_vals, 25),
        percentile_50=_percentile(sorted_vals, 50),
        percentile_75=_percentile(sorted_vals, 75),
        percentile_95=_percentile(sorted_vals, 95),
    )
