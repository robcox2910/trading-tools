"""Terminal output formatters and chart rendering for the backtester CLI.

Centralise all output logic -- metric printing, Monte Carlo tables,
walk-forward tables, and chart rendering -- so that the individual
command modules remain focused on orchestration.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import typer

from trading_tools.apps.backtester.charts import (
    create_benchmark_chart,
    create_comparison_chart,
    create_dashboard,
    create_monte_carlo_chart,
    create_walk_forward_chart,
    save_charts,
    show_charts,
)

if TYPE_CHECKING:
    from pathlib import Path

    from trading_tools.apps.backtester.monte_carlo import MonteCarloResult
    from trading_tools.apps.backtester.walk_forward import WalkForwardResult
    from trading_tools.core.models import BacktestResult


def print_result(result: BacktestResult) -> None:
    """Print a formatted summary of the backtest result to the terminal."""
    typer.echo(f"\n{'=' * 50}")
    typer.echo(f"Strategy:        {result.strategy_name}")
    typer.echo(f"Symbol:          {result.symbol}")
    typer.echo(f"Interval:        {result.interval.value}")
    typer.echo(f"Initial Capital: {result.initial_capital}")
    typer.echo(f"Final Capital:   {result.final_capital}")
    typer.echo(f"Trades:          {len(result.trades)}")
    if result.metrics:
        typer.echo(f"\n{'--- Metrics ---':^50}")
        for key, value in result.metrics.items():
            typer.echo(f"  {key:20s}: {value:.6f}")
    typer.echo(f"{'=' * 50}\n")


def print_monte_carlo(mc_result: MonteCarloResult) -> None:
    """Print a formatted Monte Carlo distribution table."""
    typer.echo(f"\n{'=' * 70}")
    typer.echo(f"Monte Carlo Simulation — {mc_result.num_shuffles} shuffles")
    typer.echo(f"{'=' * 70}")

    header = f"{'Metric':<16} {'Mean':>10} {'Std':>10} {'P5':>10} {'P25':>10} {'P50':>10} {'P75':>10} {'P95':>10}"
    typer.echo(header)
    typer.echo("-" * len(header))

    for dist in mc_result.distributions:
        typer.echo(
            f"{dist.metric_name:<16} "
            f"{dist.mean:>10.4f} "
            f"{dist.std:>10.4f} "
            f"{dist.percentile_5:>10.4f} "
            f"{dist.percentile_25:>10.4f} "
            f"{dist.percentile_50:>10.4f} "
            f"{dist.percentile_75:>10.4f} "
            f"{dist.percentile_95:>10.4f}"
        )
    typer.echo(f"{'=' * 70}\n")


def print_walk_forward(wf_result: WalkForwardResult) -> None:
    """Print a formatted walk-forward result table."""
    typer.echo(f"\n{'=' * 60}")
    typer.echo(f"Walk-Forward Optimisation — {wf_result.symbol} ({wf_result.interval.value})")
    typer.echo(f"{'=' * 60}")

    header = f"{'Fold':<6} {'Strategy':<20} {'Train Return%':>14} {'Test Return%':>14}"
    typer.echo(header)
    typer.echo("-" * len(header))

    for fold in wf_result.folds:
        train_ret = float(fold.train_result.metrics.get("total_return", Decimal(0))) * 100
        test_ret = float(fold.test_result.metrics.get("total_return", Decimal(0))) * 100
        typer.echo(
            f"{fold.fold_index:<6} "
            f"{fold.best_strategy_name:<20} "
            f"{train_ret:>13.2f}% "
            f"{test_ret:>13.2f}%"
        )

    typer.echo(f"\n{'--- Aggregate Metrics ---':^60}")
    for key, value in wf_result.aggregate_metrics.items():
        typer.echo(f"  {key:20s}: {value:.6f}")
    typer.echo(f"{'=' * 60}\n")


def render_run_charts(
    result: BacktestResult,
    benchmark_result: BacktestResult | None,
    *,
    chart: bool,
    chart_output: Path | None,
) -> None:
    """Build and display/save charts for the run command."""
    if not result.trades:
        typer.echo("No trades — skipping charts.")
        return

    figs = [create_dashboard(result)]
    if benchmark_result is not None and benchmark_result.trades:
        figs.append(create_benchmark_chart(result, benchmark_result))

    if chart_output is not None:
        save_charts(figs, chart_output)
        typer.echo(f"Charts saved to {chart_output}")
    elif chart:
        show_charts(figs)


def render_compare_charts(
    results: list[BacktestResult],
    *,
    chart: bool,  # noqa: ARG001
    chart_output: Path | None,
) -> None:
    """Build and display/save charts for the compare command."""
    has_trades = any(r.trades for r in results)
    if not has_trades:
        typer.echo("No trades — skipping charts.")
        return

    fig = create_comparison_chart(results)
    if chart_output is not None:
        save_charts([fig], chart_output)
        typer.echo(f"Charts saved to {chart_output}")
    else:
        show_charts([fig])


def render_monte_carlo_charts(
    mc_result: MonteCarloResult,
    *,
    chart: bool,  # noqa: ARG001
    chart_output: Path | None,
) -> None:
    """Build and display/save charts for the monte-carlo command."""
    fig = create_monte_carlo_chart(mc_result)
    if chart_output is not None:
        save_charts([fig], chart_output)
        typer.echo(f"Charts saved to {chart_output}")
    else:
        show_charts([fig])


def render_walk_forward_charts(
    wf_result: WalkForwardResult,
    *,
    chart: bool,  # noqa: ARG001
    chart_output: Path | None,
) -> None:
    """Build and display/save charts for the walk-forward command."""
    fig = create_walk_forward_chart(wf_result)
    if chart_output is not None:
        save_charts([fig], chart_output)
        typer.echo(f"Charts saved to {chart_output}")
    else:
        show_charts([fig])
