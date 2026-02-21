"""Tests for the strategy comparison module."""

from decimal import Decimal

import pytest

from trading_tools.apps.backtester.compare import (
    _SORT_METRICS,
    format_comparison_table,
    run_comparison,
)
from trading_tools.apps.backtester.strategy_factory import STRATEGY_NAMES
from trading_tools.core.models import (
    BacktestResult,
    Candle,
    ExecutionConfig,
    Interval,
    RiskConfig,
)

EXPECTED_STRATEGY_COUNT = 10


def _candle(ts: int, close: str, volume: str = "100") -> Candle:
    """Build a candle with sensible defaults for testing."""
    c = Decimal(close)
    return Candle(
        symbol="BTC-USD",
        timestamp=ts,
        open=c,
        high=c + Decimal(5),
        low=c - Decimal(5),
        close=c,
        volume=Decimal(volume),
        interval=Interval.H1,
    )


# Generate enough candles for all strategies to have meaningful data.
# 30 candles covers the longest default look-back (MACD slow=26 + signal=9).
_TEST_CANDLES = [_candle(1000 + i * 3600, str(100 + i)) for i in range(40)]


class StubProvider:
    """Stub candle provider returning pre-configured candles."""

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
        """Return pre-configured candles ignoring filter parameters."""
        return self._candles


class TestRunComparison:
    """Tests for run_comparison()."""

    @pytest.mark.asyncio
    async def test_returns_result_for_every_strategy(self) -> None:
        """Verify run_comparison returns one result per strategy."""
        results = await run_comparison(
            provider=StubProvider(_TEST_CANDLES),
            symbol="BTC-USD",
            interval=Interval.H1,
            capital=Decimal(10000),
            execution_config=ExecutionConfig(),
            risk_config=RiskConfig(),
            start=0,
            end=2**53,
        )
        assert len(results) == EXPECTED_STRATEGY_COUNT

    @pytest.mark.asyncio
    async def test_all_strategy_names_present(self) -> None:
        """Verify every registered strategy name prefix appears in the results."""
        results = await run_comparison(
            provider=StubProvider(_TEST_CANDLES),
            symbol="BTC-USD",
            interval=Interval.H1,
            capital=Decimal(10000),
            execution_config=ExecutionConfig(),
            risk_config=RiskConfig(),
            start=0,
            end=2**53,
        )
        result_names = {r.strategy_name for r in results}
        for name in STRATEGY_NAMES:
            assert any(rn.startswith(name) for rn in result_names), (
                f"No result strategy name starts with '{name}'"
            )

    @pytest.mark.asyncio
    async def test_results_contain_metrics(self) -> None:
        """Verify each result has expected metric keys."""
        results = await run_comparison(
            provider=StubProvider(_TEST_CANDLES),
            symbol="BTC-USD",
            interval=Interval.H1,
            capital=Decimal(10000),
            execution_config=ExecutionConfig(),
            risk_config=RiskConfig(),
            start=0,
            end=2**53,
        )
        for result in results:
            assert "total_return" in result.metrics
            assert "win_rate" in result.metrics
            assert "sharpe_ratio" in result.metrics


def _make_result(name: str, total_return: str, max_drawdown: str = "0.05") -> BacktestResult:
    """Build a minimal BacktestResult for table-formatting tests."""
    return BacktestResult(
        strategy_name=name,
        symbol="BTC-USD",
        interval=Interval.H1,
        initial_capital=Decimal(10000),
        final_capital=Decimal(10000) * (Decimal(1) + Decimal(total_return)),
        trades=(),
        metrics={
            "total_return": Decimal(total_return),
            "win_rate": Decimal("0.6"),
            "profit_factor": Decimal("1.5"),
            "max_drawdown": Decimal(max_drawdown),
            "sharpe_ratio": Decimal("1.2"),
            "total_trades": Decimal(10),
            "total_fees": Decimal("5.00"),
        },
    )


class TestFormatComparisonTable:
    """Tests for format_comparison_table()."""

    def test_table_contains_header_row(self) -> None:
        """Verify the table includes all column headers."""
        results = [_make_result("sma_crossover", "0.15")]
        table = format_comparison_table(results)
        assert "Strategy" in table
        assert "Return%" in table
        assert "Trades" in table
        assert "Win Rate" in table
        assert "Profit F" in table
        assert "Max DD" in table
        assert "Sharpe" in table
        assert "Fees" in table

    def test_table_contains_all_strategies(self) -> None:
        """Verify every strategy name appears in the output."""
        results = [
            _make_result("sma_crossover", "0.15"),
            _make_result("rsi", "0.10"),
            _make_result("bollinger", "0.20"),
        ]
        table = format_comparison_table(results)
        for r in results:
            assert r.strategy_name in table

    def test_default_sort_by_total_return(self) -> None:
        """Verify default ranking is descending by total_return."""
        results = [
            _make_result("low", "0.05"),
            _make_result("high", "0.25"),
            _make_result("mid", "0.15"),
        ]
        table = format_comparison_table(results)
        lines = table.strip().split("\n")
        # Data rows are lines[3:-1] (after separator, header, separator)
        data_lines = lines[3:-1]
        assert "high" in data_lines[0]
        assert "mid" in data_lines[1]
        assert "low" in data_lines[2]

    def test_invalid_sort_metric_raises(self) -> None:
        """Verify ValueError is raised for unknown sort metric."""
        with pytest.raises(ValueError, match="Unknown sort metric"):
            format_comparison_table([], sort_by="nonexistent")


class TestSortByMetric:
    """Tests for sort_by parameter in format_comparison_table."""

    def test_sort_by_max_drawdown_ascending(self) -> None:
        """Verify max_drawdown sorts ascending (lower DD ranks higher)."""
        results = [
            _make_result("high_dd", "0.10", max_drawdown="0.30"),
            _make_result("low_dd", "0.10", max_drawdown="0.05"),
            _make_result("mid_dd", "0.10", max_drawdown="0.15"),
        ]
        table = format_comparison_table(results, sort_by="max_drawdown")
        lines = table.strip().split("\n")
        data_lines = lines[3:-1]
        assert "low_dd" in data_lines[0]
        assert "mid_dd" in data_lines[1]
        assert "high_dd" in data_lines[2]

    def test_sort_by_win_rate_descending(self) -> None:
        """Verify win_rate sorts descending (higher WR ranks higher)."""
        r1 = _make_result("low_wr", "0.10")
        r1.metrics["win_rate"] = Decimal("0.3")
        r2 = _make_result("high_wr", "0.10")
        r2.metrics["win_rate"] = Decimal("0.9")

        table = format_comparison_table([r1, r2], sort_by="win_rate")
        lines = table.strip().split("\n")
        data_lines = lines[3:-1]
        assert "high_wr" in data_lines[0]
        assert "low_wr" in data_lines[1]

    def test_all_sort_metrics_are_valid(self) -> None:
        """Verify every metric in _SORT_METRICS produces a valid table."""
        results = [_make_result("test", "0.10")]
        for metric in _SORT_METRICS:
            table = format_comparison_table(results, sort_by=metric)
            assert "test" in table
