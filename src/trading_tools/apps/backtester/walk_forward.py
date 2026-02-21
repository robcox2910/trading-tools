"""Walk-forward optimisation for the backtester.

Split historical candle data into rolling train/test windows, select
the best-performing strategy on each training window, and evaluate it
on the following test window. This simulates a realistic out-of-sample
validation process where strategy selection is always made on past data.

"Optimisation" here means strategy **selection** among all registered
strategies with fixed parameters — not parameter grid search. The best
strategy per fold is the one with the highest value of a chosen metric
(e.g. total return) on the training window.
"""

from dataclasses import dataclass
from decimal import Decimal

from trading_tools.apps.backtester.engine import BacktestEngine
from trading_tools.apps.backtester.metrics import calculate_metrics
from trading_tools.apps.backtester.strategy_factory import STRATEGY_NAMES, build_strategy
from trading_tools.core.models import (
    BacktestResult,
    Candle,
    ExecutionConfig,
    Interval,
    RiskConfig,
)


@dataclass(frozen=True)
class WalkForwardFold:
    """Result of a single walk-forward fold.

    Capture the fold index, the name of the strategy selected during
    training, and the full backtest results for both the training and
    test windows.
    """

    fold_index: int
    best_strategy_name: str
    train_result: BacktestResult
    test_result: BacktestResult


@dataclass(frozen=True)
class WalkForwardResult:
    """Aggregate result of walk-forward optimisation across all folds.

    Bundle per-fold details, aggregate performance metrics computed
    from all test-window trades, and the symbol/interval context.
    """

    folds: tuple[WalkForwardFold, ...]
    aggregate_metrics: dict[str, Decimal]
    symbol: str
    interval: Interval


class _SliceProvider:
    """Candle provider that returns a pre-sliced list of candles.

    Used internally by walk-forward to feed a subset of candles to
    the backtest engine without re-fetching from the original source.
    """

    def __init__(self, candles: list[Candle]) -> None:
        """Initialize with a fixed list of candles."""
        self._candles = candles

    async def get_candles(
        self,
        symbol: str,  # noqa: ARG002
        interval: Interval,  # noqa: ARG002
        start_ts: int,  # noqa: ARG002
        end_ts: int,  # noqa: ARG002
    ) -> list[Candle]:
        """Return the pre-sliced candles ignoring filter parameters."""
        return self._candles


async def run_walk_forward(
    *,
    candles: list[Candle],
    symbol: str,
    interval: Interval,
    initial_capital: Decimal,
    execution_config: ExecutionConfig,
    risk_config: RiskConfig,
    train_window: int,
    test_window: int,
    step: int,
    sort_metric: str = "total_return",
    strategy_params: dict[str, int | float],
) -> WalkForwardResult:
    """Run walk-forward optimisation on pre-fetched candle data.

    For each fold, train all strategies on the training window, pick
    the best by ``sort_metric``, then evaluate it on the test window.
    Aggregate all test-window trades into a single set of metrics.

    Args:
        candles: Full list of candles (pre-fetched by the caller).
        symbol: Trading pair (e.g. ``BTC-USD``).
        interval: Candle time interval.
        initial_capital: Starting capital for each fold.
        execution_config: Fees, slippage, and position-sizing config.
        risk_config: Stop-loss and take-profit thresholds.
        train_window: Number of candles in each training window.
        test_window: Number of candles in each test window.
        step: Number of candles to advance between folds.
        sort_metric: Metric key to rank strategies by during training.
        strategy_params: Strategy configuration parameters passed to
            ``build_strategy`` (e.g. short_period, long_period, etc.).

    Returns:
        A ``WalkForwardResult`` with per-fold details and aggregate metrics.

    Raises:
        ValueError: If insufficient candles for even one fold.

    """
    min_candles = train_window + test_window
    if len(candles) < min_candles:
        msg = (
            f"Need at least {min_candles} candles for walk-forward "
            f"(train={train_window} + test={test_window}), got {len(candles)}"
        )
        raise ValueError(msg)

    folds: list[WalkForwardFold] = []
    fold_index = 0
    offset = 0

    while offset + train_window + test_window <= len(candles):
        train_candles = candles[offset : offset + train_window]
        test_candles = candles[offset + train_window : offset + train_window + test_window]

        best_name, train_result = await _find_best_strategy(
            train_candles,
            symbol=symbol,
            interval=interval,
            initial_capital=initial_capital,
            execution_config=execution_config,
            risk_config=risk_config,
            sort_metric=sort_metric,
            strategy_params=strategy_params,
        )

        best_strategy = build_strategy(best_name, **strategy_params)  # type: ignore[arg-type]
        test_provider = _SliceProvider(test_candles)
        test_engine = BacktestEngine(
            provider=test_provider,
            strategy=best_strategy,
            initial_capital=initial_capital,
            execution_config=execution_config,
            risk_config=risk_config,
        )
        test_result = await test_engine.run(symbol, interval, 0, 2**53)

        folds.append(
            WalkForwardFold(
                fold_index=fold_index,
                best_strategy_name=best_name,
                train_result=train_result,
                test_result=test_result,
            )
        )

        fold_index += 1
        offset += step

    all_test_trades = [t for fold in folds for t in fold.test_result.trades]
    final_capital = initial_capital + sum((t.pnl for t in all_test_trades), Decimal(0))
    aggregate_metrics = calculate_metrics(all_test_trades, initial_capital, final_capital)

    return WalkForwardResult(
        folds=tuple(folds),
        aggregate_metrics=aggregate_metrics,
        symbol=symbol,
        interval=interval,
    )


async def _find_best_strategy(
    candles: list[Candle],
    *,
    symbol: str,
    interval: Interval,
    initial_capital: Decimal,
    execution_config: ExecutionConfig,
    risk_config: RiskConfig,
    sort_metric: str,
    strategy_params: dict[str, int | float],
) -> tuple[str, BacktestResult]:
    """Run all strategies on a candle slice and return the best.

    Args:
        candles: Training window candles.
        symbol: Trading pair.
        interval: Candle time interval.
        initial_capital: Starting capital.
        execution_config: Execution cost configuration.
        risk_config: Risk management configuration.
        sort_metric: Metric to rank by.
        strategy_params: Strategy parameters.

    Returns:
        Tuple of (best strategy name from STRATEGY_NAMES, its BacktestResult).

    """
    best_name = STRATEGY_NAMES[0]
    best_result: BacktestResult | None = None
    best_value = Decimal("-Infinity")

    provider = _SliceProvider(candles)

    for name in STRATEGY_NAMES:
        strategy = build_strategy(name, **strategy_params)  # type: ignore[arg-type]
        engine = BacktestEngine(
            provider=provider,
            strategy=strategy,
            initial_capital=initial_capital,
            execution_config=execution_config,
            risk_config=risk_config,
        )
        result = await engine.run(symbol, interval, 0, 2**53)
        value = result.metrics.get(sort_metric, Decimal(0))
        if best_result is None or value > best_value:
            best_value = value
            best_name = name
            best_result = result

    if best_result is None:  # pragma: no cover — STRATEGY_NAMES is never empty
        msg = "No strategies registered"
        raise RuntimeError(msg)
    return best_name, best_result
