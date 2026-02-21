# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportUnhashable=false
"""Test suite for the backtester charting module.

Verify that each chart function returns a valid Plotly ``Figure`` with
the expected traces, annotations, and error handling. Tests use
synthetic candle and trade data to exercise all chart types.
"""

from decimal import Decimal
from pathlib import Path

import plotly.graph_objects as go
import pytest

from trading_tools.apps.backtester.charts import (
    create_comparison_chart,
    create_dashboard,
    create_drawdown_chart,
    create_equity_curve,
    create_pnl_distribution,
    create_price_chart,
    save_charts,
)
from trading_tools.core.models import (
    BacktestResult,
    Candle,
    Interval,
    Side,
    Trade,
)

_NUM_SAMPLE_CANDLES = 20
_BASE_PRICE = 100
_PRICE_INCREMENT = 2
_CANDLE_SPREAD = 5
_VOLUME = Decimal(1000)
_INITIAL_CAPITAL = Decimal(10_000)
_HOUR_SECONDS = 3600
_BASE_TS = 1_000_000

_EXPECTED_DASHBOARD_SUBPLOT_TITLES = 4
_EQUITY_TRACE_COUNT = 1
_CANDLESTICK_TRACE_INDEX = 0


def _candle(ts: int, close: str) -> Candle:
    """Build a synthetic candle at the given timestamp and close price."""
    c = Decimal(close)
    return Candle(
        symbol="BTC-USD",
        timestamp=ts,
        open=c - Decimal(1),
        high=c + Decimal(_CANDLE_SPREAD),
        low=c - Decimal(_CANDLE_SPREAD),
        close=c,
        volume=_VOLUME,
        interval=Interval.H1,
    )


def _sample_candles() -> tuple[Candle, ...]:
    """Generate 20 candles with an upward-trending price."""
    return tuple(
        _candle(
            _BASE_TS + i * _HOUR_SECONDS,
            str(_BASE_PRICE + i * _PRICE_INCREMENT),
        )
        for i in range(_NUM_SAMPLE_CANDLES)
    )


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
    _trade("100", "110", _BASE_TS, _BASE_TS + _HOUR_SECONDS * 3),
    _trade("112", "105", _BASE_TS + _HOUR_SECONDS * 5, _BASE_TS + _HOUR_SECONDS * 8),
    _trade("106", "120", _BASE_TS + _HOUR_SECONDS * 10, _BASE_TS + _HOUR_SECONDS * 15),
    _trade("121", "115", _BASE_TS + _HOUR_SECONDS * 16, _BASE_TS + _HOUR_SECONDS * 18),
)


def _make_result(
    *,
    trades: tuple[Trade, ...] = _SAMPLE_TRADES,
    candles: tuple[Candle, ...] | None = None,
    strategy_name: str = "test_strategy",
) -> BacktestResult:
    """Build a BacktestResult with the given trades and candles."""
    if candles is None:
        candles = _sample_candles()

    final_capital = _INITIAL_CAPITAL + sum(t.pnl for t in trades)
    return BacktestResult(
        strategy_name=strategy_name,
        symbol="BTC-USD",
        interval=Interval.H1,
        initial_capital=_INITIAL_CAPITAL,
        final_capital=final_capital,
        trades=trades,
        metrics={
            "total_return": Decimal("0.06"),
            "win_rate": Decimal("0.5"),
            "sharpe_ratio": Decimal("1.1"),
            "profit_factor": Decimal("1.4"),
            "max_drawdown": Decimal("0.07"),
            "total_trades": Decimal(len(trades)),
        },
        candles=candles,
    )


class TestEquityCurve:
    """Test the equity curve chart creation."""

    def test_returns_figure(self) -> None:
        """Return a Plotly Figure instance."""
        fig = create_equity_curve(_make_result())
        assert isinstance(fig, go.Figure)

    def test_contains_scatter_trace(self) -> None:
        """Contain a Scatter trace for the equity line."""
        fig = create_equity_curve(_make_result())
        scatter_traces = [t for t in fig.data if isinstance(t, go.Scatter)]
        assert len(scatter_traces) >= _EQUITY_TRACE_COUNT

    def test_starts_at_initial_capital(self) -> None:
        """Start the equity series at the initial capital value."""
        fig = create_equity_curve(_make_result())
        scatter = next(t for t in fig.data if isinstance(t, go.Scatter))
        assert scatter.y is not None
        assert scatter.y[0] == pytest.approx(float(_INITIAL_CAPITAL))

    def test_ends_at_final_capital(self) -> None:
        """End the equity series at the computed final capital."""
        result = _make_result()
        fig = create_equity_curve(result)
        scatter = next(t for t in fig.data if isinstance(t, go.Scatter))
        assert scatter.y is not None
        assert scatter.y[-1] == pytest.approx(float(result.final_capital))

    def test_raises_on_no_trades(self) -> None:
        """Raise ValueError when the result has no trades."""
        result = _make_result(trades=())
        with pytest.raises(ValueError, match="no trades"):
            create_equity_curve(result)


class TestDrawdownChart:
    """Test the drawdown chart creation."""

    def test_returns_figure(self) -> None:
        """Return a Plotly Figure instance."""
        fig = create_drawdown_chart(_make_result())
        assert isinstance(fig, go.Figure)

    def test_all_values_non_positive(self) -> None:
        """Ensure all drawdown values are zero or negative."""
        fig = create_drawdown_chart(_make_result())
        scatter = next(t for t in fig.data if isinstance(t, go.Scatter))
        assert scatter.y is not None
        assert all(v <= 0 for v in scatter.y)

    def test_max_drawdown_annotated(self) -> None:
        """Annotate the maximum drawdown point on the chart."""
        fig = create_drawdown_chart(_make_result())
        annotations = fig.layout.annotations
        assert len(annotations) >= 1
        assert any("Max DD" in str(a.text) for a in annotations)

    def test_raises_on_no_trades(self) -> None:
        """Raise ValueError when the result has no trades."""
        result = _make_result(trades=())
        with pytest.raises(ValueError, match="no trades"):
            create_drawdown_chart(result)


class TestPriceChart:
    """Test the candlestick price chart creation."""

    def test_returns_figure(self) -> None:
        """Return a Plotly Figure instance."""
        fig = create_price_chart(_make_result())
        assert isinstance(fig, go.Figure)

    def test_contains_candlestick_trace(self) -> None:
        """Contain a Candlestick trace for the price data."""
        fig = create_price_chart(_make_result())
        candlestick_traces = [t for t in fig.data if isinstance(t, go.Candlestick)]
        assert len(candlestick_traces) == 1

    def test_has_entry_exit_markers(self) -> None:
        """Overlay entry and exit scatter markers on the chart."""
        fig = create_price_chart(_make_result())
        scatter_traces = [t for t in fig.data if isinstance(t, go.Scatter)]
        names = {t.name for t in scatter_traces}
        assert "Entry" in names
        assert "Exit" in names

    def test_raises_on_no_candles(self) -> None:
        """Raise ValueError when the result has no candles."""
        result = _make_result(candles=())
        with pytest.raises(ValueError, match="no candles"):
            create_price_chart(result)


class TestPnLDistribution:
    """Test the PnL distribution histogram chart creation."""

    def test_returns_figure(self) -> None:
        """Return a Plotly Figure instance."""
        fig = create_pnl_distribution(_make_result())
        assert isinstance(fig, go.Figure)

    def test_contains_histogram_trace(self) -> None:
        """Contain at least one Histogram trace."""
        fig = create_pnl_distribution(_make_result())
        hist_traces = [t for t in fig.data if isinstance(t, go.Histogram)]
        assert len(hist_traces) >= 1

    def test_has_winners_and_losers(self) -> None:
        """Include separate histogram traces for winners and losers."""
        fig = create_pnl_distribution(_make_result())
        hist_traces = [t for t in fig.data if isinstance(t, go.Histogram)]
        names = {t.name for t in hist_traces}
        assert "Winners" in names
        assert "Losers" in names

    def test_raises_on_no_trades(self) -> None:
        """Raise ValueError when the result has no trades."""
        result = _make_result(trades=())
        with pytest.raises(ValueError, match="no trades"):
            create_pnl_distribution(result)


class TestComparisonChart:
    """Test the strategy comparison bar chart creation."""

    def test_returns_figure(self) -> None:
        """Return a Plotly Figure instance."""
        results = [_make_result(strategy_name="A"), _make_result(strategy_name="B")]
        fig = create_comparison_chart(results)
        assert isinstance(fig, go.Figure)

    def test_contains_bar_traces(self) -> None:
        """Contain Bar traces for each compared metric."""
        results = [_make_result(strategy_name="A"), _make_result(strategy_name="B")]
        fig = create_comparison_chart(results)
        bar_traces = [t for t in fig.data if isinstance(t, go.Bar)]
        assert len(bar_traces) >= 1

    def test_raises_on_empty_list(self) -> None:
        """Raise ValueError when the results list is empty."""
        with pytest.raises(ValueError, match="no results"):
            create_comparison_chart([])

    def test_respects_custom_metrics(self) -> None:
        """Use the custom metrics tuple when provided."""
        results = [_make_result(strategy_name="A")]
        custom = ("total_return", "win_rate")
        fig = create_comparison_chart(results, metrics=custom)
        bar_traces = [t for t in fig.data if isinstance(t, go.Bar)]
        assert len(bar_traces) == len(custom)


class TestDashboard:
    """Test the 2x2 dashboard subplot creation."""

    def test_returns_figure(self) -> None:
        """Return a Plotly Figure instance."""
        fig = create_dashboard(_make_result())
        assert isinstance(fig, go.Figure)

    def test_contains_expected_subplot_titles(self) -> None:
        """Contain the expected number of subplot titles."""
        fig = create_dashboard(_make_result())
        titles = [
            a.text
            for a in fig.layout.annotations
            if hasattr(a, "text")
            and a.text in {"Equity Curve", "Drawdown", "Price", "PnL Distribution"}
        ]
        assert len(titles) == _EXPECTED_DASHBOARD_SUBPLOT_TITLES

    def test_raises_on_no_trades(self) -> None:
        """Raise ValueError when the result has no trades."""
        result = _make_result(trades=())
        with pytest.raises(ValueError, match="no trades"):
            create_dashboard(result)


class TestSaveCharts:
    """Test saving charts to HTML files."""

    def test_creates_html_file(self, tmp_path: Path) -> None:
        """Write an HTML file at the given path."""
        fig = create_equity_curve(_make_result())
        output = tmp_path / "charts.html"
        save_charts([fig], output)
        assert output.exists()
        content = output.read_text()
        assert "<html>" in content

    def test_raises_on_non_html_path(self, tmp_path: Path) -> None:
        """Raise ValueError when the path does not end with .html."""
        fig = create_equity_curve(_make_result())
        output = tmp_path / "charts.pdf"
        with pytest.raises(ValueError, match=r"\.html"):
            save_charts([fig], output)
