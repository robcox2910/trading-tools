"""Tests for the backtester CLI output formatters."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    import pytest

from trading_tools.apps.backtester.cli._output import (
    print_monte_carlo,
    print_result,
    print_walk_forward,
    render_compare_charts,
    render_monte_carlo_charts,
    render_run_charts,
    render_walk_forward_charts,
)
from trading_tools.apps.backtester.monte_carlo import (
    MonteCarloDistribution,
    MonteCarloResult,
)
from trading_tools.apps.backtester.walk_forward import (
    WalkForwardFold,
    WalkForwardResult,
)
from trading_tools.core.models import BacktestResult, Interval, Side, Trade

_STRATEGY = "test_strategy"
_SYMBOL = "BTC-USD"


def _make_trade() -> Trade:
    """Create a minimal Trade for test results."""
    return Trade(
        symbol=_SYMBOL,
        side=Side.BUY,
        quantity=Decimal(1),
        entry_price=Decimal(100),
        exit_price=Decimal(110),
        entry_time=1000,
        exit_time=2000,
    )


def _make_result(
    *,
    with_trades: bool = True,
    metrics: dict[str, Decimal] | None = None,
) -> BacktestResult:
    """Create a test BacktestResult."""
    return BacktestResult(
        strategy_name=_STRATEGY,
        symbol=_SYMBOL,
        interval=Interval.H1,
        initial_capital=Decimal(1000),
        final_capital=Decimal(1100),
        trades=(_make_trade(),) if with_trades else (),
        metrics=metrics or {"total_return": Decimal("0.10")},
    )


def _make_mc_result() -> MonteCarloResult:
    """Create a test MonteCarloResult with one distribution."""
    dist = MonteCarloDistribution(
        metric_name="total_return",
        mean=0.05,
        std=0.02,
        percentile_5=-0.01,
        percentile_25=0.03,
        percentile_50=0.05,
        percentile_75=0.07,
        percentile_95=0.10,
    )
    return MonteCarloResult(
        num_shuffles=100,
        original=_make_result(),
        distributions=(dist,),
    )


def _make_wf_result() -> WalkForwardResult:
    """Create a test WalkForwardResult with one fold."""
    fold = WalkForwardFold(
        fold_index=0,
        best_strategy_name=_STRATEGY,
        train_result=_make_result(),
        test_result=_make_result(),
    )
    return WalkForwardResult(
        folds=(fold,),
        aggregate_metrics={"mean_return": Decimal("0.10")},
        symbol=_SYMBOL,
        interval=Interval.H1,
    )


class TestPrintResult:
    """Tests for the print_result formatter."""

    def test_prints_strategy_and_capital(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Verify strategy name and capital figures appear in output."""
        result = _make_result()

        print_result(result)
        output = capsys.readouterr().out

        assert _STRATEGY in output
        assert "1000" in output
        assert "1100" in output

    def test_prints_metrics(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Verify metrics are printed."""
        result = _make_result(metrics={"total_return": Decimal("0.123456")})

        print_result(result)
        output = capsys.readouterr().out

        assert "total_return" in output
        assert "0.123456" in output

    def test_prints_without_metrics(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Verify output works with empty metrics."""
        result = _make_result(metrics={})

        print_result(result)
        output = capsys.readouterr().out

        assert _STRATEGY in output


class TestPrintMonteCarlo:
    """Tests for the print_monte_carlo formatter."""

    def test_prints_distribution_table(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Verify Monte Carlo table headers and distribution rows appear."""
        mc_result = _make_mc_result()

        print_monte_carlo(mc_result)
        output = capsys.readouterr().out

        assert "Monte Carlo" in output
        assert "100 shuffles" in output
        assert "total_return" in output
        assert "Mean" in output
        assert "P95" in output


class TestPrintWalkForward:
    """Tests for the print_walk_forward formatter."""

    def test_prints_fold_table(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Verify walk-forward table headers and fold rows appear."""
        wf_result = _make_wf_result()

        print_walk_forward(wf_result)
        output = capsys.readouterr().out

        assert "Walk-Forward" in output
        assert _STRATEGY in output
        assert "mean_return" in output
        assert "Fold" in output


class TestRenderRunCharts:
    """Tests for render_run_charts."""

    @patch("trading_tools.apps.backtester.cli._output.create_dashboard")
    def test_no_trades_skips_charts(
        self,
        mock_dashboard: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Skip chart creation when result has no trades."""
        result = _make_result(with_trades=False)

        render_run_charts(result, None, chart=True, chart_output=None)
        output = capsys.readouterr().out

        assert "No trades" in output
        mock_dashboard.assert_not_called()

    @patch("trading_tools.apps.backtester.cli._output.show_charts")
    @patch("trading_tools.apps.backtester.cli._output.create_dashboard")
    def test_chart_flag_shows_charts(
        self,
        mock_dashboard: MagicMock,
        mock_show: MagicMock,
    ) -> None:
        """Display charts when chart=True and no output path."""
        mock_dashboard.return_value = MagicMock()
        result = _make_result()

        render_run_charts(result, None, chart=True, chart_output=None)

        mock_show.assert_called_once()

    @patch("trading_tools.apps.backtester.cli._output.save_charts")
    @patch("trading_tools.apps.backtester.cli._output.create_dashboard")
    def test_chart_output_saves_to_file(
        self,
        mock_dashboard: MagicMock,
        mock_save: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Save charts to file when chart_output is provided."""
        mock_dashboard.return_value = MagicMock()
        result = _make_result()

        render_run_charts(result, None, chart=True, chart_output=Path("output/charts.html"))
        output = capsys.readouterr().out

        mock_save.assert_called_once()
        assert "saved" in output.lower()

    @patch("trading_tools.apps.backtester.cli._output.create_benchmark_chart")
    @patch("trading_tools.apps.backtester.cli._output.show_charts")
    @patch("trading_tools.apps.backtester.cli._output.create_dashboard")
    def test_includes_benchmark_chart(
        self,
        mock_dashboard: MagicMock,
        mock_show: MagicMock,
        mock_benchmark: MagicMock,
    ) -> None:
        """Include benchmark chart when benchmark result has trades."""
        mock_dashboard.return_value = MagicMock()
        mock_benchmark.return_value = MagicMock()
        result = _make_result()
        benchmark = _make_result()

        render_run_charts(result, benchmark, chart=True, chart_output=None)

        mock_benchmark.assert_called_once()
        figs = mock_show.call_args[0][0]
        assert len(figs) == 2  # noqa: PLR2004


class TestRenderCompareCharts:
    """Tests for render_compare_charts."""

    @patch("trading_tools.apps.backtester.cli._output.create_comparison_chart")
    def test_no_trades_skips_charts(
        self,
        mock_chart: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Skip charts when no results have trades."""
        results = [_make_result(with_trades=False)]

        render_compare_charts(results, chart=True, chart_output=None)
        output = capsys.readouterr().out

        assert "No trades" in output
        mock_chart.assert_not_called()

    @patch("trading_tools.apps.backtester.cli._output.show_charts")
    @patch("trading_tools.apps.backtester.cli._output.create_comparison_chart")
    def test_shows_comparison_chart(
        self,
        mock_chart: MagicMock,
        mock_show: MagicMock,
    ) -> None:
        """Display comparison chart interactively."""
        mock_chart.return_value = MagicMock()
        results = [_make_result()]

        render_compare_charts(results, chart=True, chart_output=None)

        mock_show.assert_called_once()

    @patch("trading_tools.apps.backtester.cli._output.save_charts")
    @patch("trading_tools.apps.backtester.cli._output.create_comparison_chart")
    def test_saves_comparison_chart(
        self,
        mock_chart: MagicMock,
        mock_save: MagicMock,
    ) -> None:
        """Save comparison chart to file."""
        mock_chart.return_value = MagicMock()
        results = [_make_result()]

        render_compare_charts(results, chart=True, chart_output=Path("output/compare.html"))

        mock_save.assert_called_once()


class TestRenderMonteCarloCharts:
    """Tests for render_monte_carlo_charts."""

    @patch("trading_tools.apps.backtester.cli._output.show_charts")
    @patch("trading_tools.apps.backtester.cli._output.create_monte_carlo_chart")
    def test_shows_monte_carlo_chart(
        self,
        mock_chart: MagicMock,
        mock_show: MagicMock,
    ) -> None:
        """Display Monte Carlo chart interactively."""
        mock_chart.return_value = MagicMock()

        render_monte_carlo_charts(_make_mc_result(), chart=True, chart_output=None)

        mock_show.assert_called_once()

    @patch("trading_tools.apps.backtester.cli._output.save_charts")
    @patch("trading_tools.apps.backtester.cli._output.create_monte_carlo_chart")
    def test_saves_monte_carlo_chart(
        self,
        mock_chart: MagicMock,
        mock_save: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Save Monte Carlo chart to file."""
        mock_chart.return_value = MagicMock()

        render_monte_carlo_charts(
            _make_mc_result(), chart=True, chart_output=Path("output/mc.html")
        )
        output = capsys.readouterr().out

        mock_save.assert_called_once()
        assert "saved" in output.lower()


class TestRenderWalkForwardCharts:
    """Tests for render_walk_forward_charts."""

    @patch("trading_tools.apps.backtester.cli._output.show_charts")
    @patch("trading_tools.apps.backtester.cli._output.create_walk_forward_chart")
    def test_shows_walk_forward_chart(
        self,
        mock_chart: MagicMock,
        mock_show: MagicMock,
    ) -> None:
        """Display walk-forward chart interactively."""
        mock_chart.return_value = MagicMock()

        render_walk_forward_charts(_make_wf_result(), chart=True, chart_output=None)

        mock_show.assert_called_once()

    @patch("trading_tools.apps.backtester.cli._output.save_charts")
    @patch("trading_tools.apps.backtester.cli._output.create_walk_forward_chart")
    def test_saves_walk_forward_chart(
        self,
        mock_chart: MagicMock,
        mock_save: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Save walk-forward chart to file."""
        mock_chart.return_value = MagicMock()

        render_walk_forward_charts(
            _make_wf_result(), chart=True, chart_output=Path("output/wf.html")
        )
        output = capsys.readouterr().out

        mock_save.assert_called_once()
        assert "saved" in output.lower()
