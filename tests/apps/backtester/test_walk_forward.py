"""Test suite for the walk-forward optimisation module."""

from decimal import Decimal

import pytest

from trading_tools.apps.backtester.strategy_factory import STRATEGY_NAMES
from trading_tools.apps.backtester.walk_forward import (
    WalkForwardResult,
    run_walk_forward,
)
from trading_tools.core.models import Candle, ExecutionConfig, Interval, RiskConfig

_INITIAL_CAPITAL = Decimal(10_000)
_DEFAULT_PARAMS: dict[str, int | float] = {
    "short_period": 10,
    "long_period": 20,
    "period": 14,
    "overbought": 70,
    "oversold": 30,
    "num_std": 2.0,
    "fast_period": 12,
    "slow_period": 26,
    "signal_period": 9,
    "k_period": 14,
    "d_period": 3,
    "z_threshold": 2.0,
}
_HOUR_SECONDS = 3600
_EXPECTED_TWO_FOLDS = 2


def _candle(ts: int, close: str) -> Candle:
    """Build a candle with sensible defaults for testing."""
    c = Decimal(close)
    return Candle(
        symbol="BTC-USD",
        timestamp=ts,
        open=c,
        high=c + Decimal(5),
        low=c - Decimal(5),
        close=c,
        volume=Decimal(100),
        interval=Interval.H1,
    )


def _make_candles(count: int) -> list[Candle]:
    """Generate ascending-price candles for testing."""
    return [_candle(1000 + i * _HOUR_SECONDS, str(100 + i)) for i in range(count)]


class TestRunWalkForward:
    """Test the run_walk_forward function."""

    @pytest.mark.asyncio
    async def test_insufficient_candles_raises(self) -> None:
        """Raise ValueError when candles are fewer than train + test."""
        candles = _make_candles(10)
        with pytest.raises(ValueError, match="Need at least"):
            await run_walk_forward(
                candles=candles,
                symbol="BTC-USD",
                interval=Interval.H1,
                initial_capital=_INITIAL_CAPITAL,
                execution_config=ExecutionConfig(),
                risk_config=RiskConfig(),
                train_window=50,
                test_window=50,
                step=50,
                strategy_params=_DEFAULT_PARAMS,
            )

    @pytest.mark.asyncio
    async def test_single_fold_produces_one_fold(self) -> None:
        """Produce exactly one fold when candles match train + test."""
        candles = _make_candles(80)
        result = await run_walk_forward(
            candles=candles,
            symbol="BTC-USD",
            interval=Interval.H1,
            initial_capital=_INITIAL_CAPITAL,
            execution_config=ExecutionConfig(),
            risk_config=RiskConfig(),
            train_window=40,
            test_window=40,
            step=40,
            strategy_params=_DEFAULT_PARAMS,
        )
        assert isinstance(result, WalkForwardResult)
        assert len(result.folds) == 1

    @pytest.mark.asyncio
    async def test_best_strategy_is_registered(self) -> None:
        """Select a strategy name from the registered STRATEGY_NAMES."""
        candles = _make_candles(80)
        result = await run_walk_forward(
            candles=candles,
            symbol="BTC-USD",
            interval=Interval.H1,
            initial_capital=_INITIAL_CAPITAL,
            execution_config=ExecutionConfig(),
            risk_config=RiskConfig(),
            train_window=40,
            test_window=40,
            step=40,
            strategy_params=_DEFAULT_PARAMS,
        )
        for fold in result.folds:
            assert fold.best_strategy_name in STRATEGY_NAMES

    @pytest.mark.asyncio
    async def test_aggregate_metrics_have_expected_keys(self) -> None:
        """Include standard metric keys in the aggregate."""
        candles = _make_candles(80)
        result = await run_walk_forward(
            candles=candles,
            symbol="BTC-USD",
            interval=Interval.H1,
            initial_capital=_INITIAL_CAPITAL,
            execution_config=ExecutionConfig(),
            risk_config=RiskConfig(),
            train_window=40,
            test_window=40,
            step=40,
            strategy_params=_DEFAULT_PARAMS,
        )
        assert "total_return" in result.aggregate_metrics
        assert "win_rate" in result.aggregate_metrics
        assert "sharpe_ratio" in result.aggregate_metrics

    @pytest.mark.asyncio
    async def test_multiple_folds_with_step(self) -> None:
        """Produce multiple folds when step allows sliding forward."""
        candles = _make_candles(120)
        result = await run_walk_forward(
            candles=candles,
            symbol="BTC-USD",
            interval=Interval.H1,
            initial_capital=_INITIAL_CAPITAL,
            execution_config=ExecutionConfig(),
            risk_config=RiskConfig(),
            train_window=40,
            test_window=40,
            step=40,
            strategy_params=_DEFAULT_PARAMS,
        )
        assert len(result.folds) == _EXPECTED_TWO_FOLDS

    @pytest.mark.asyncio
    async def test_symbol_and_interval_stored(self) -> None:
        """Store the symbol and interval in the result."""
        candles = _make_candles(80)
        result = await run_walk_forward(
            candles=candles,
            symbol="BTC-USD",
            interval=Interval.H1,
            initial_capital=_INITIAL_CAPITAL,
            execution_config=ExecutionConfig(),
            risk_config=RiskConfig(),
            train_window=40,
            test_window=40,
            step=40,
            strategy_params=_DEFAULT_PARAMS,
        )
        assert result.symbol == "BTC-USD"
        assert result.interval == Interval.H1
