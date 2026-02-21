# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false
"""Interactive Plotly charts for visualising backtest results.

Provide functions to create equity curves, drawdown charts, candlestick
price charts with trade markers, PnL distributions, strategy comparison
bar charts, and combined dashboards. All charts use a consistent dark
theme and can be displayed in the browser or saved to HTML files.
"""

from __future__ import annotations

import tempfile
import webbrowser
from decimal import Decimal
from typing import TYPE_CHECKING

import plotly.graph_objects as go
from plotly.subplots import make_subplots

if TYPE_CHECKING:
    from pathlib import Path

    from trading_tools.apps.backtester.monte_carlo import MonteCarloResult
    from trading_tools.apps.backtester.walk_forward import WalkForwardResult
    from trading_tools.core.models import BacktestResult


def build_equity_series(result: BacktestResult) -> tuple[list[int], list[float]]:
    """Reconstruct the equity curve from initial capital and cumulative trade PnL.

    Walk through trades in order, accumulating PnL onto the starting
    capital. Return parallel lists of timestamps and equity values.

    Args:
        result: A completed backtest result containing trades.

    Returns:
        A tuple of (timestamps, equity_values) lists.

    """
    capital = float(result.initial_capital)
    timestamps: list[int] = [result.trades[0].entry_time]
    equity: list[float] = [capital]
    for trade in result.trades:
        capital += float(trade.pnl)
        timestamps.append(trade.exit_time)
        equity.append(capital)
    return timestamps, equity


def build_drawdown_series(equity: list[float]) -> list[float]:
    """Compute drawdown fractions from an equity series.

    At each point, calculate the fractional decline from the running
    peak. All values are zero or negative.

    Args:
        equity: List of equity values over time.

    Returns:
        List of drawdown fractions (zero or negative).

    """
    peak = equity[0]
    drawdowns: list[float] = []
    for value in equity:
        peak = max(peak, value)
        dd = (value - peak) / peak if peak > 0 else 0.0
        drawdowns.append(dd)
    return drawdowns


_BG_COLOR = "#1e1e2f"
_PAPER_COLOR = "#1e1e2f"
_GRID_COLOR = "#2e2e3e"
_TEXT_COLOR = "#e0e0e0"
_GREEN = "#00c853"
_RED = "#ff1744"
_REFERENCE_DASH = "dash"


def _apply_dark_theme(fig: go.Figure) -> go.Figure:
    """Apply a consistent dark theme to a Plotly figure.

    Set the background colour, text colour, grid styling, and legend
    positioning so that all charts share a uniform dark appearance.

    Args:
        fig: The Plotly figure to style.

    Returns:
        The same figure, mutated in place, for chaining convenience.

    """
    fig.update_layout(
        template="plotly_dark",
        plot_bgcolor=_BG_COLOR,
        paper_bgcolor=_PAPER_COLOR,
        font_color=_TEXT_COLOR,
        legend={"bgcolor": "rgba(0,0,0,0)"},
        margin={"l": 60, "r": 30, "t": 50, "b": 40},
    )
    fig.update_xaxes(gridcolor=_GRID_COLOR, zeroline=False)
    fig.update_yaxes(gridcolor=_GRID_COLOR, zeroline=False)
    return fig


def create_equity_curve(result: BacktestResult) -> go.Figure:
    """Create a line chart of portfolio value over time.

    Reconstruct the equity curve from the initial capital and cumulative
    trade PnL. The line is coloured green when the strategy is profitable
    overall, red otherwise. A dashed reference line marks the initial
    capital level.

    Args:
        result: A completed backtest result containing trades.

    Returns:
        A Plotly ``Figure`` with the equity curve.

    Raises:
        ValueError: If the result contains no trades.

    """
    if not result.trades:
        msg = "Cannot create equity curve: no trades in result"
        raise ValueError(msg)

    timestamps, equity = build_equity_series(result)

    overall_return = equity[-1] - equity[0]
    line_color = _GREEN if overall_return >= 0 else _RED

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=timestamps,
            y=equity,
            mode="lines",
            name="Equity",
            line={"color": line_color, "width": 2},
        )
    )
    fig.add_hline(
        y=float(result.initial_capital),
        line_dash=_REFERENCE_DASH,
        line_color=_TEXT_COLOR,
        opacity=0.5,
        annotation_text="Initial Capital",
    )
    fig.update_layout(
        title=f"Equity Curve — {result.strategy_name}",
        xaxis_title="Time",
        yaxis_title="Portfolio Value",
    )
    return _apply_dark_theme(fig)


def create_drawdown_chart(result: BacktestResult) -> go.Figure:
    """Create a filled area chart of drawdown percentage from peak equity.

    Compute the running peak and drawdown at each trade exit. All
    drawdown values are zero or negative. The point of maximum drawdown
    is annotated on the chart.

    Args:
        result: A completed backtest result containing trades.

    Returns:
        A Plotly ``Figure`` with the drawdown chart.

    Raises:
        ValueError: If the result contains no trades.

    """
    if not result.trades:
        msg = "Cannot create drawdown chart: no trades in result"
        raise ValueError(msg)

    timestamps, equity = build_equity_series(result)
    drawdowns = build_drawdown_series(equity)

    min_dd = min(drawdowns)
    min_dd_idx = drawdowns.index(min_dd)

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=timestamps,
            y=drawdowns,
            mode="lines",
            fill="tozeroy",
            name="Drawdown",
            line={"color": _RED, "width": 1},
            fillcolor="rgba(255, 23, 68, 0.3)",
        )
    )
    fig.add_annotation(
        x=timestamps[min_dd_idx],
        y=min_dd,
        text=f"Max DD: {min_dd:.2%}",
        showarrow=True,
        arrowhead=2,
        font={"color": _RED},
    )
    fig.update_layout(
        title=f"Drawdown — {result.strategy_name}",
        xaxis_title="Time",
        yaxis_title="Drawdown %",
    )
    return _apply_dark_theme(fig)


def create_price_chart(result: BacktestResult) -> go.Figure:
    """Create a candlestick OHLC chart with trade entry and exit markers.

    Plot the candle data as a standard candlestick chart and overlay
    green triangle-up markers at buy entry points and red triangle-down
    markers at sell exit points.

    Args:
        result: A completed backtest result containing candles and trades.

    Returns:
        A Plotly ``Figure`` with the price chart.

    Raises:
        ValueError: If the result contains no candles.

    """
    if not result.candles:
        msg = "Cannot create price chart: no candles in result"
        raise ValueError(msg)

    timestamps = [c.timestamp for c in result.candles]
    opens = [float(c.open) for c in result.candles]
    highs = [float(c.high) for c in result.candles]
    lows = [float(c.low) for c in result.candles]
    closes = [float(c.close) for c in result.candles]

    fig = go.Figure()
    fig.add_trace(
        go.Candlestick(
            x=timestamps,
            open=opens,
            high=highs,
            low=lows,
            close=closes,
            name="Price",
            increasing_line_color=_GREEN,
            decreasing_line_color=_RED,
        )
    )

    if result.trades:
        entry_times = [t.entry_time for t in result.trades]
        entry_prices = [float(t.entry_price) for t in result.trades]
        exit_times = [t.exit_time for t in result.trades]
        exit_prices = [float(t.exit_price) for t in result.trades]

        fig.add_trace(
            go.Scatter(
                x=entry_times,
                y=entry_prices,
                mode="markers",
                name="Entry",
                marker={
                    "symbol": "triangle-up",
                    "size": 12,
                    "color": _GREEN,
                },
            )
        )
        fig.add_trace(
            go.Scatter(
                x=exit_times,
                y=exit_prices,
                mode="markers",
                name="Exit",
                marker={
                    "symbol": "triangle-down",
                    "size": 12,
                    "color": _RED,
                },
            )
        )

    fig.update_layout(
        title=f"Price — {result.symbol} ({result.strategy_name})",
        xaxis_title="Time",
        yaxis_title="Price",
        xaxis_rangeslider_visible=False,
    )
    return _apply_dark_theme(fig)


def create_pnl_distribution(result: BacktestResult) -> go.Figure:
    """Create a histogram of trade PnL percentages.

    Separate winning and losing trades into green and red bars. Draw a
    vertical line at the mean PnL percentage.

    Args:
        result: A completed backtest result containing trades.

    Returns:
        A Plotly ``Figure`` with the PnL histogram.

    Raises:
        ValueError: If the result contains no trades.

    """
    if not result.trades:
        msg = "Cannot create PnL distribution: no trades in result"
        raise ValueError(msg)

    pnl_pcts = [float(t.pnl_pct) * 100 for t in result.trades]
    winners = [p for p in pnl_pcts if p >= 0]
    losers = [p for p in pnl_pcts if p < 0]

    fig = go.Figure()
    if winners:
        fig.add_trace(go.Histogram(x=winners, name="Winners", marker_color=_GREEN, opacity=0.8))
    if losers:
        fig.add_trace(go.Histogram(x=losers, name="Losers", marker_color=_RED, opacity=0.8))

    mean_pnl = sum(pnl_pcts) / len(pnl_pcts)
    fig.add_vline(
        x=mean_pnl,
        line_dash=_REFERENCE_DASH,
        line_color=_TEXT_COLOR,
        annotation_text=f"Mean: {mean_pnl:.2f}%",
    )

    fig.update_layout(
        title=f"PnL Distribution — {result.strategy_name}",
        xaxis_title="PnL %",
        yaxis_title="Count",
        barmode="overlay",
    )
    return _apply_dark_theme(fig)


_BLUE = "#2196f3"


def create_benchmark_chart(
    strategy_result: BacktestResult,
    benchmark_result: BacktestResult,
) -> go.Figure:
    """Create an equity curve overlay comparing a strategy against a benchmark.

    Plot the strategy equity curve in green (if profitable) or red,
    and the benchmark equity curve as a dashed blue line. Both curves
    start at the same initial capital to allow direct comparison.

    Args:
        strategy_result: The active strategy backtest result.
        benchmark_result: The buy-and-hold benchmark result.

    Returns:
        A Plotly ``Figure`` with both equity curves overlaid.

    Raises:
        ValueError: If either result contains no trades.

    """
    if not strategy_result.trades:
        msg = "Cannot create benchmark chart: strategy has no trades"
        raise ValueError(msg)
    if not benchmark_result.trades:
        msg = "Cannot create benchmark chart: benchmark has no trades"
        raise ValueError(msg)

    strat_ts, strat_eq = build_equity_series(strategy_result)
    bench_ts, bench_eq = build_equity_series(benchmark_result)

    overall_return = strat_eq[-1] - strat_eq[0]
    strat_color = _GREEN if overall_return >= 0 else _RED

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=strat_ts,
            y=strat_eq,
            mode="lines",
            name=strategy_result.strategy_name,
            line={"color": strat_color, "width": 2},
        )
    )
    fig.add_trace(
        go.Scatter(
            x=bench_ts,
            y=bench_eq,
            mode="lines",
            name=benchmark_result.strategy_name,
            line={"color": _BLUE, "width": 2, "dash": "dash"},
        )
    )
    fig.add_hline(
        y=float(strategy_result.initial_capital),
        line_dash=_REFERENCE_DASH,
        line_color=_TEXT_COLOR,
        opacity=0.5,
        annotation_text="Initial Capital",
    )
    fig.update_layout(
        title=f"Strategy vs Benchmark — {strategy_result.symbol}",
        xaxis_title="Time",
        yaxis_title="Portfolio Value",
    )
    return _apply_dark_theme(fig)


_DEFAULT_COMPARISON_METRICS = ("total_return", "win_rate", "sharpe_ratio", "profit_factor")


def create_comparison_chart(
    results: list[BacktestResult],
    metrics: tuple[str, ...] | None = None,
) -> go.Figure:
    """Create a grouped bar chart comparing strategies across metrics.

    Each strategy gets one bar per metric. The default metrics are
    total return, win rate, Sharpe ratio, and profit factor.

    Args:
        results: A list of backtest results, one per strategy.
        metrics: Optional tuple of metric keys to compare. Defaults to
            ``("total_return", "win_rate", "sharpe_ratio", "profit_factor")``.

    Returns:
        A Plotly ``Figure`` with the comparison chart.

    Raises:
        ValueError: If the results list is empty.

    """
    if not results:
        msg = "Cannot create comparison chart: no results provided"
        raise ValueError(msg)

    selected = metrics or _DEFAULT_COMPARISON_METRICS
    strategy_names = [r.strategy_name for r in results]

    fig = go.Figure()
    for metric in selected:
        values = [float(r.metrics.get(metric, Decimal(0))) for r in results]
        fig.add_trace(go.Bar(name=metric, x=strategy_names, y=values))

    fig.update_layout(
        title="Strategy Comparison",
        xaxis_title="Strategy",
        yaxis_title="Value",
        barmode="group",
    )
    return _apply_dark_theme(fig)


def create_monte_carlo_chart(mc_result: MonteCarloResult) -> go.Figure:
    """Create histograms of Monte Carlo metric distributions.

    For each metric (total return, max drawdown, Sharpe ratio), draw a
    histogram of the reshuffled values with a vertical line marking the
    original backtest value.

    Args:
        mc_result: The completed Monte Carlo simulation result.

    Returns:
        A Plotly ``Figure`` with one subplot per metric.

    """
    dists = mc_result.distributions
    num_metrics = len(dists)

    fig = make_subplots(
        rows=1,
        cols=num_metrics,
        subplot_titles=tuple(d.metric_name.replace("_", " ").title() for d in dists),
    )

    original_metrics = mc_result.original.metrics

    for i, dist in enumerate(dists, start=1):
        values = [
            dist.percentile_5,
            dist.percentile_25,
            dist.percentile_50,
            dist.percentile_75,
            dist.percentile_95,
        ]
        fig.add_trace(
            go.Bar(
                x=[f"P{p}" for p in (5, 25, 50, 75, 95)],
                y=values,
                name=dist.metric_name.replace("_", " ").title(),
                marker_color=_GREEN,
                opacity=0.8,
                showlegend=False,
            ),
            row=1,
            col=i,
        )
        original_val = float(original_metrics.get(dist.metric_name, Decimal(0)))
        fig.add_hline(
            y=original_val,
            line_dash=_REFERENCE_DASH,
            line_color=_RED,
            annotation_text=f"Original: {original_val:.4f}",
        )

    fig.update_layout(
        title_text=f"Monte Carlo — {mc_result.original.strategy_name} ({mc_result.num_shuffles} shuffles)",
        height=400,
    )
    return _apply_dark_theme(fig)


def create_walk_forward_chart(wf_result: WalkForwardResult) -> go.Figure:
    """Create a bar chart of per-fold test returns colour-coded by strategy.

    Each bar represents one walk-forward fold's test-window total return,
    labelled with the strategy that was selected during training.

    Args:
        wf_result: The completed walk-forward optimisation result.

    Returns:
        A Plotly ``Figure`` with one bar per fold.

    """
    fold_labels = [f"Fold {f.fold_index}" for f in wf_result.folds]
    returns = [
        float(f.test_result.metrics.get("total_return", Decimal(0))) * 100 for f in wf_result.folds
    ]
    colors = [_GREEN if r >= 0 else _RED for r in returns]
    hover_text = [f.best_strategy_name for f in wf_result.folds]

    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=fold_labels,
            y=returns,
            marker_color=colors,
            text=hover_text,
            textposition="outside",
            name="Test Return %",
        )
    )
    fig.update_layout(
        title_text=f"Walk-Forward — {wf_result.symbol} ({wf_result.interval.value})",
        xaxis_title="Fold",
        yaxis_title="Test Return %",
    )
    return _apply_dark_theme(fig)


def create_dashboard(result: BacktestResult) -> go.Figure:
    """Create a 2x2 subplot dashboard combining four key charts.

    Combine the equity curve, drawdown chart, price chart, and PnL
    distribution into a single figure. If candles are unavailable the
    price subplot is left empty.

    Args:
        result: A completed backtest result with trades and optionally
            candles.

    Returns:
        A Plotly ``Figure`` with four subplots.

    Raises:
        ValueError: If the result contains no trades.

    """
    if not result.trades:
        msg = "Cannot create dashboard: no trades in result"
        raise ValueError(msg)

    fig = make_subplots(
        rows=2,
        cols=2,
        subplot_titles=("Equity Curve", "Drawdown", "Price", "PnL Distribution"),
    )

    # --- Equity curve (row 1, col 1) ---
    eq_ts, eq_vals = build_equity_series(result)

    overall_return = eq_vals[-1] - eq_vals[0]
    fig.add_trace(
        go.Scatter(
            x=eq_ts,
            y=eq_vals,
            mode="lines",
            name="Equity",
            line={"color": _GREEN if overall_return >= 0 else _RED, "width": 2},
            showlegend=False,
        ),
        row=1,
        col=1,
    )

    # --- Drawdown (row 1, col 2) ---
    drawdowns = build_drawdown_series(eq_vals)

    fig.add_trace(
        go.Scatter(
            x=eq_ts,
            y=drawdowns,
            mode="lines",
            fill="tozeroy",
            name="Drawdown",
            line={"color": _RED, "width": 1},
            fillcolor="rgba(255, 23, 68, 0.3)",
            showlegend=False,
        ),
        row=1,
        col=2,
    )

    # --- Price chart (row 2, col 1) ---
    if result.candles:
        timestamps = [c.timestamp for c in result.candles]
        fig.add_trace(
            go.Candlestick(
                x=timestamps,
                open=[float(c.open) for c in result.candles],
                high=[float(c.high) for c in result.candles],
                low=[float(c.low) for c in result.candles],
                close=[float(c.close) for c in result.candles],
                name="Price",
                showlegend=False,
                increasing_line_color=_GREEN,
                decreasing_line_color=_RED,
            ),
            row=2,
            col=1,
        )

        entry_times = [t.entry_time for t in result.trades]
        entry_prices = [float(t.entry_price) for t in result.trades]
        exit_times = [t.exit_time for t in result.trades]
        exit_prices = [float(t.exit_price) for t in result.trades]
        fig.add_trace(
            go.Scatter(
                x=entry_times,
                y=entry_prices,
                mode="markers",
                name="Entry",
                marker={"symbol": "triangle-up", "size": 10, "color": _GREEN},
                showlegend=False,
            ),
            row=2,
            col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=exit_times,
                y=exit_prices,
                mode="markers",
                name="Exit",
                marker={"symbol": "triangle-down", "size": 10, "color": _RED},
                showlegend=False,
            ),
            row=2,
            col=1,
        )

    # --- PnL distribution (row 2, col 2) ---
    pnl_pcts = [float(t.pnl_pct) * 100 for t in result.trades]
    winners = [p for p in pnl_pcts if p >= 0]
    losers = [p for p in pnl_pcts if p < 0]
    if winners:
        fig.add_trace(
            go.Histogram(
                x=winners, name="Winners", marker_color=_GREEN, opacity=0.8, showlegend=False
            ),
            row=2,
            col=2,
        )
    if losers:
        fig.add_trace(
            go.Histogram(x=losers, name="Losers", marker_color=_RED, opacity=0.8, showlegend=False),
            row=2,
            col=2,
        )

    fig.update_layout(
        title_text=f"Dashboard — {result.strategy_name} ({result.symbol})",
        height=800,
    )
    # Disable range slider on candlestick subplots
    fig.update_xaxes(rangeslider_visible=False, row=2, col=1)

    return _apply_dark_theme(fig)


def show_charts(figs: list[go.Figure]) -> None:
    """Write figures to a temporary HTML file and open it in the browser.

    Combine all figures into a single HTML page so they can be viewed
    together. The temp file is not automatically deleted, allowing the
    browser to load it fully.

    Args:
        figs: A list of Plotly figures to display.

    """
    html_parts = [
        "<html><head><title>Backtest Charts</title></head><body>",
        *[fig.to_html(full_html=False, include_plotlyjs="cdn") for fig in figs],
        "</body></html>",
    ]

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".html", delete=False, prefix="backtest_"
    ) as tmp:
        tmp.write("\n".join(html_parts))
        tmp_path = tmp.name

    webbrowser.open(f"file://{tmp_path}")


def save_charts(figs: list[go.Figure], output_path: Path) -> None:
    """Save figures to an HTML file at the given path.

    Combine all figures into a single HTML page and write it to disk.

    Args:
        figs: A list of Plotly figures to save.
        output_path: Destination file path. Must have a ``.html`` suffix.

    Raises:
        ValueError: If the output path does not end with ``.html``.

    """
    if output_path.suffix.lower() != ".html":
        msg = f"Output path must end with .html, got: {output_path}"
        raise ValueError(msg)

    html_parts = [
        "<html><head><title>Backtest Charts</title></head><body>",
        *[fig.to_html(full_html=False, include_plotlyjs="cdn") for fig in figs],
        "</body></html>",
    ]

    output_path.write_text("\n".join(html_parts))
