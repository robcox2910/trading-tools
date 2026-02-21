# pyright: reportUnknownMemberType=false
"""Test suite for the Monte Carlo simulation module."""

from decimal import Decimal

import pytest

from trading_tools.apps.backtester.monte_carlo import (
    MonteCarloResult,
    run_monte_carlo,
)
from trading_tools.core.models import BacktestResult, Interval, Side, Trade

_INITIAL_CAPITAL = Decimal(10_000)
_SEED = 42
_NUM_SHUFFLES = 200


def _trade(entry: str, exit_: str, entry_ts: int, exit_ts: int) -> Trade:
    """Build a trade with the given entry/exit prices and timestamps."""
    return Trade(
        symbol="BTC-USD",
        side=Side.BUY,
        quantity=Decimal(1),
        entry_price=Decimal(entry),
        entry_time=entry_ts,
        exit_price=Decimal(exit_),
        exit_time=exit_ts,
    )


_SAMPLE_TRADES = (
    _trade("100", "110", 1000, 2000),
    _trade("112", "105", 3000, 4000),
    _trade("106", "120", 5000, 6000),
    _trade("121", "115", 7000, 8000),
    _trade("116", "125", 9000, 10000),
)


def _make_result(trades: tuple[Trade, ...] = _SAMPLE_TRADES) -> BacktestResult:
    """Build a minimal BacktestResult for Monte Carlo tests."""
    final = _INITIAL_CAPITAL + sum(t.pnl for t in trades)
    return BacktestResult(
        strategy_name="test_strategy",
        symbol="BTC-USD",
        interval=Interval.H1,
        initial_capital=_INITIAL_CAPITAL,
        final_capital=final,
        trades=trades,
    )


class TestRunMonteCarlo:
    """Test the run_monte_carlo function."""

    def test_returns_monte_carlo_result(self) -> None:
        """Return a MonteCarloResult instance."""
        mc = run_monte_carlo(_make_result(), num_shuffles=_NUM_SHUFFLES, seed=_SEED)
        assert isinstance(mc, MonteCarloResult)

    def test_raises_on_fewer_than_two_trades(self) -> None:
        """Raise ValueError when the result has fewer than 2 trades."""
        single_trade = (_SAMPLE_TRADES[0],)
        with pytest.raises(ValueError, match="at least 2 trades"):
            run_monte_carlo(_make_result(trades=single_trade))

    def test_raises_on_zero_trades(self) -> None:
        """Raise ValueError when the result has no trades."""
        with pytest.raises(ValueError, match="at least 2 trades"):
            run_monte_carlo(_make_result(trades=()))

    def test_num_shuffles_stored(self) -> None:
        """Store the requested number of shuffles in the result."""
        expected_shuffles = 50
        mc = run_monte_carlo(_make_result(), num_shuffles=expected_shuffles, seed=_SEED)
        assert mc.num_shuffles == expected_shuffles

    def test_original_result_preserved(self) -> None:
        """Preserve the original BacktestResult in the output."""
        result = _make_result()
        mc = run_monte_carlo(result, num_shuffles=_NUM_SHUFFLES, seed=_SEED)
        assert mc.original is result

    def test_deterministic_with_seed(self) -> None:
        """Produce identical results with the same seed."""
        result = _make_result()
        mc1 = run_monte_carlo(result, num_shuffles=_NUM_SHUFFLES, seed=_SEED)
        mc2 = run_monte_carlo(result, num_shuffles=_NUM_SHUFFLES, seed=_SEED)
        for d1, d2 in zip(mc1.distributions, mc2.distributions, strict=True):
            assert d1.mean == d2.mean
            assert d1.percentile_50 == d2.percentile_50

    def test_distributions_contain_expected_metrics(self) -> None:
        """Include distributions for total_return, max_drawdown, and sharpe_ratio."""
        mc = run_monte_carlo(_make_result(), num_shuffles=_NUM_SHUFFLES, seed=_SEED)
        metric_names = {d.metric_name for d in mc.distributions}
        assert metric_names == {"total_return", "max_drawdown", "sharpe_ratio"}


class TestPercentileOrdering:
    """Verify percentile values are in monotonic order."""

    def test_percentiles_are_ordered(self) -> None:
        """Ensure p5 <= p25 <= p50 <= p75 <= p95 for each distribution."""
        mc = run_monte_carlo(_make_result(), num_shuffles=_NUM_SHUFFLES, seed=_SEED)
        for dist in mc.distributions:
            assert dist.percentile_5 <= dist.percentile_25
            assert dist.percentile_25 <= dist.percentile_50
            assert dist.percentile_50 <= dist.percentile_75
            assert dist.percentile_75 <= dist.percentile_95


class TestMeanTotalReturn:
    """Verify shuffling preserves total PnL."""

    def test_mean_total_return_close_to_original(self) -> None:
        """Confirm mean total return approximately equals the original.

        Shuffling trades preserves the sum of PnL, so total return
        should be identical across all permutations.
        """
        result = _make_result()
        mc = run_monte_carlo(result, num_shuffles=_NUM_SHUFFLES, seed=_SEED)
        total_return_dist = next(d for d in mc.distributions if d.metric_name == "total_return")
        # With no fees, shuffling preserves exact total return
        # The mean should match the original since PnL sum is invariant
        assert total_return_dist.mean == pytest.approx(
            float((result.final_capital - result.initial_capital) / result.initial_capital),
            abs=1e-10,
        )
        # std should be zero since total return is invariant to order
        assert total_return_dist.std == pytest.approx(0.0, abs=1e-10)
