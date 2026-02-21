"""Strategy comparison utilities for the backtester.

Run all available strategies against the same candle data and produce a
ranked summary table. This keeps comparison logic separate from the
single-strategy ``run`` module.
"""

from decimal import Decimal

from trading_tools.apps.backtester.engine import BacktestEngine
from trading_tools.apps.backtester.strategy_factory import STRATEGY_NAMES, build_strategy
from trading_tools.core.models import BacktestResult, ExecutionConfig, Interval, RiskConfig
from trading_tools.core.protocols import CandleProvider

_SORT_METRICS = (
    "total_return",
    "win_rate",
    "profit_factor",
    "max_drawdown",
    "sharpe_ratio",
    "total_trades",
    "total_fees",
)

# Metrics where lower is better (sort ascending instead of descending).
_ASCENDING_METRICS = frozenset({"max_drawdown", "total_fees"})


async def run_comparison(  # noqa: PLR0913
    *,
    provider: CandleProvider,
    symbol: str,
    interval: Interval,
    capital: Decimal,
    execution_config: ExecutionConfig,
    risk_config: RiskConfig,
    start: int,
    end: int,
    short_period: int = 10,
    long_period: int = 20,
    period: int = 14,
    overbought: int = 70,
    oversold: int = 30,
    num_std: float = 2.0,
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
    k_period: int = 14,
    d_period: int = 3,
    z_threshold: float = 2.0,
) -> list[BacktestResult]:
    """Run every registered strategy against the same candle data.

    Build each strategy from ``STRATEGY_NAMES``, execute it through
    a ``BacktestEngine``, and collect all results. The caller supplies
    a single ``CandleProvider`` so every strategy is evaluated on
    identical data.

    Args:
        provider: Data source for historical candles.
        symbol: Trading pair (e.g. ``BTC-USD``).
        interval: Candle time interval.
        capital: Initial portfolio capital in quote currency.
        execution_config: Fees, slippage, and position-sizing config.
        risk_config: Stop-loss and take-profit thresholds.
        start: Start Unix timestamp in seconds.
        end: End Unix timestamp in seconds.
        short_period: Short period for SMA/EMA crossover strategies.
        long_period: Long period for SMA/EMA crossover strategies.
        period: Period for RSI, Bollinger, VWAP, Donchian, Mean Reversion.
        overbought: Overbought threshold for RSI/Stochastic.
        oversold: Oversold threshold for RSI/Stochastic.
        num_std: Standard deviations for Bollinger Bands.
        fast_period: MACD fast EMA period.
        slow_period: MACD slow EMA period.
        signal_period: MACD signal EMA period.
        k_period: Stochastic %K period.
        d_period: Stochastic %D period.
        z_threshold: Mean reversion z-score threshold.

    Returns:
        A list of ``BacktestResult`` objects, one per strategy.

    """
    results: list[BacktestResult] = []

    for name in STRATEGY_NAMES:
        strategy = build_strategy(
            name,
            short_period=short_period,
            long_period=long_period,
            period=period,
            overbought=overbought,
            oversold=oversold,
            num_std=num_std,
            fast_period=fast_period,
            slow_period=slow_period,
            signal_period=signal_period,
            k_period=k_period,
            d_period=d_period,
            z_threshold=z_threshold,
        )
        engine = BacktestEngine(
            provider=provider,
            strategy=strategy,
            initial_capital=capital,
            execution_config=execution_config,
            risk_config=risk_config,
        )
        result = await engine.run(symbol, interval, start, end)
        results.append(result)

    return results


def format_comparison_table(
    results: list[BacktestResult],
    sort_by: str = "total_return",
) -> str:
    """Format backtest results as a ranked comparison table.

    Sort the results by the chosen metric and render a plain-text table
    with columns for rank, strategy name, and key performance figures.

    Args:
        results: List of ``BacktestResult`` objects to compare.
        sort_by: Metric key to rank by. For ``max_drawdown`` and
            ``total_fees`` lower values rank higher (ascending sort);
            all other metrics sort descending.

    Returns:
        A multi-line string containing the formatted table.

    Raises:
        ValueError: If ``sort_by`` is not a recognised metric name.

    """
    if sort_by not in _SORT_METRICS:
        msg = f"Unknown sort metric: {sort_by}. Must be one of: {', '.join(_SORT_METRICS)}"
        raise ValueError(msg)

    reverse = sort_by not in _ASCENDING_METRICS
    sorted_results = sorted(
        results,
        key=lambda r: r.metrics.get(sort_by, Decimal(0)),
        reverse=reverse,
    )

    header = (
        f"{'Rank':<5} "
        f"{'Strategy':<17} "
        f"{'Return%':>9} "
        f"{'Trades':>7} "
        f"{'Win Rate':>9} "
        f"{'Profit F':>9} "
        f"{'Max DD':>9} "
        f"{'Sharpe':>9} "
        f"{'Fees':>12}"
    )
    separator = "-" * len(header)

    lines = [separator, header, separator]

    for rank, result in enumerate(sorted_results, start=1):
        m = result.metrics
        return_pct = m.get("total_return", Decimal(0)) * 100
        trades = int(m.get("total_trades", Decimal(0)))
        wr = m.get("win_rate", Decimal(0)) * 100
        pf = m.get("profit_factor", Decimal(0))
        dd = m.get("max_drawdown", Decimal(0)) * 100
        sr = m.get("sharpe_ratio", Decimal(0))
        fees = m.get("total_fees", Decimal(0))

        pf_str = "Inf" if pf == Decimal("Infinity") else f"{pf:.4f}"

        lines.append(
            f"{rank:<5} "
            f"{result.strategy_name:<17} "
            f"{return_pct:>8.2f}% "
            f"{trades:>7} "
            f"{wr:>8.2f}% "
            f"{pf_str:>9} "
            f"{dd:>8.2f}% "
            f"{sr:>9.4f} "
            f"{fees:>12.4f}"
        )

    lines.append(separator)
    return "\n".join(lines)
