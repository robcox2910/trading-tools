"""Tests for the backtest-snipe CLI command."""

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
import typer
from typer.testing import CliRunner

from trading_tools.apps.polymarket.backtest_common import parse_date
from trading_tools.apps.polymarket.cli import app
from trading_tools.apps.polymarket.cli.backtest_snipe_cmd import (
    BacktestRunner,
    _group_candles_into_windows,
    _run_backtest,
)
from trading_tools.apps.polymarket_bot.models import PaperTradingResult
from trading_tools.apps.polymarket_bot.portfolio import PaperPortfolio
from trading_tools.apps.polymarket_bot.snapshot_simulator import SnapshotSimulator
from trading_tools.apps.polymarket_bot.strategies.late_snipe import PMLateSnipeStrategy
from trading_tools.core.models import Candle, Interval

_SYMBOL = "BTC-USD"
_WINDOW_TS = 1_708_300_800


def _make_candle(
    timestamp: int,
    open_: str = "100",
    close: str = "100",
    *,
    symbol: str = _SYMBOL,
) -> Candle:
    """Create a 1-minute test candle.

    Args:
        timestamp: Unix epoch seconds.
        open_: Open price.
        close: Close price.
        symbol: Trading pair symbol.

    Returns:
        Candle for testing.

    """
    o = Decimal(open_)
    c = Decimal(close)
    return Candle(
        symbol=symbol,
        timestamp=timestamp,
        open=o,
        high=max(o, c),
        low=min(o, c),
        close=c,
        volume=Decimal(1000),
        interval=Interval.M1,
    )


class TestParseDate:
    """Test the parse_date helper."""

    def test_valid_date(self) -> None:
        """Parse a valid YYYY-MM-DD date to epoch seconds."""
        ts = parse_date("2026-02-20")
        assert ts > 0

    def test_invalid_date_raises(self) -> None:
        """Raise BadParameter for invalid date format."""
        with pytest.raises(typer.BadParameter):
            parse_date("not-a-date")


class TestGroupCandlesIntoWindows:
    """Test candle grouping into 5-minute windows."""

    def test_groups_by_five_minute_alignment(self) -> None:
        """Candles are grouped into their aligned 5-minute window."""
        start = _WINDOW_TS
        end = _WINDOW_TS + 600
        candles = [
            _make_candle(start + 0),
            _make_candle(start + 60),
            _make_candle(start + 120),
            _make_candle(start + 300),
            _make_candle(start + 360),
        ]
        windows = _group_candles_into_windows(candles, start, end)

        expected_windows = 2
        assert len(windows) == expected_windows
        expected_first_count = 3
        assert len(windows[start]) == expected_first_count
        expected_second_count = 2
        assert len(windows[start + 300]) == expected_second_count

    def test_excludes_out_of_range_candles(self) -> None:
        """Candles outside [start, end) are excluded."""
        start = _WINDOW_TS
        end = _WINDOW_TS + 300
        candles = [
            _make_candle(start - 60),  # before start
            _make_candle(start + 60),  # in range
            _make_candle(end),  # at end (exclusive)
        ]
        windows = _group_candles_into_windows(candles, start, end)

        assert len(windows) == 1
        assert len(windows[start]) == 1


class TestBacktestRunner:
    """Test the BacktestRunner replay logic."""

    def test_replay_with_no_candles_returns_zero_trades(self) -> None:
        """Replay with no candle data produces zero trades."""
        strategy = PMLateSnipeStrategy(threshold=Decimal("0.80"), window_seconds=90)
        portfolio = PaperPortfolio(Decimal(1000), Decimal("0.1"))
        simulator = SnapshotSimulator()

        runner = BacktestRunner(strategy, portfolio, simulator, Decimal("0.25"))
        result = runner.replay([_SYMBOL], {_SYMBOL: []}, _WINDOW_TS, _WINDOW_TS + 300)

        assert result.snapshots_processed == 0
        assert len(result.trades) == 0

    def test_replay_with_strong_signal_produces_trades(self) -> None:
        """Replay with candles showing strong movement produces trades."""
        # Use a wide snipe window (300s = full window) so any tick can trigger
        strategy = PMLateSnipeStrategy(threshold=Decimal("0.80"), window_seconds=300)
        portfolio = PaperPortfolio(Decimal(1000), Decimal("0.5"))
        simulator = SnapshotSimulator(scale_factor=Decimal(20))

        # Create candles showing a strong upward move (4% total)
        candles = [
            _make_candle(_WINDOW_TS + 0, open_="100", close="100"),
            _make_candle(_WINDOW_TS + 60, open_="100", close="101"),
            _make_candle(_WINDOW_TS + 120, open_="101", close="102"),
            _make_candle(_WINDOW_TS + 180, open_="102", close="103"),
            _make_candle(_WINDOW_TS + 240, open_="103", close="104"),
        ]
        all_candles = {_SYMBOL: candles}

        runner = BacktestRunner(strategy, portfolio, simulator, Decimal("0.25"))
        result = runner.replay(
            [_SYMBOL],
            all_candles,
            _WINDOW_TS,
            _WINDOW_TS + 300,
        )

        expected_snapshots = 5
        assert result.snapshots_processed == expected_snapshots
        # Should have at least one open + one close trade
        min_trades = 2
        assert len(result.trades) >= min_trades

    def test_replay_tracks_wins_and_losses(self) -> None:
        """Replay correctly tracks win/loss counts in metrics."""
        strategy = PMLateSnipeStrategy(threshold=Decimal("0.80"), window_seconds=300)
        portfolio = PaperPortfolio(Decimal(10000), Decimal("0.5"))
        simulator = SnapshotSimulator(scale_factor=Decimal(20))

        # Strong upward move — should produce a YES trade that wins
        candles = [
            _make_candle(_WINDOW_TS + 0, open_="100", close="100"),
            _make_candle(_WINDOW_TS + 60, open_="100", close="102"),
            _make_candle(_WINDOW_TS + 120, open_="102", close="104"),
            _make_candle(_WINDOW_TS + 180, open_="104", close="106"),
            _make_candle(_WINDOW_TS + 240, open_="106", close="108"),
        ]
        all_candles = {_SYMBOL: candles}

        runner = BacktestRunner(strategy, portfolio, simulator, Decimal("0.25"))
        result = runner.replay(
            [_SYMBOL],
            all_candles,
            _WINDOW_TS,
            _WINDOW_TS + 300,
        )

        assert "wins" in result.metrics
        assert "losses" in result.metrics


class TestRunBacktest:
    """Test the _run_backtest integration function."""

    def test_returns_paper_trading_result(self) -> None:
        """_run_backtest returns a PaperTradingResult with correct initial capital."""
        candles = [
            _make_candle(_WINDOW_TS + 0, open_="100", close="100"),
            _make_candle(_WINDOW_TS + 60, open_="100", close="100"),
        ]
        result = _run_backtest(
            [_SYMBOL],
            {_SYMBOL: candles},
            _WINDOW_TS,
            _WINDOW_TS + 300,
            capital=Decimal(500),
            snipe_threshold=Decimal("0.80"),
            snipe_window=90,
            scale_factor=Decimal(15),
            kelly_frac=Decimal("0.25"),
            max_position_pct=Decimal("0.1"),
        )

        assert isinstance(result, PaperTradingResult)
        assert result.initial_capital == Decimal(500)
        assert result.strategy_name.startswith("pm_late_snipe")


class TestBacktestSnipeCLI:
    """Test the backtest-snipe CLI command."""

    def test_missing_dates_exits_with_error(self) -> None:
        """Exit with error when --start or --end is missing."""
        runner = CliRunner()
        result = runner.invoke(app, ["backtest-snipe"])

        assert result.exit_code == 1
        assert "required" in result.output.lower()

    def test_start_after_end_exits_with_error(self) -> None:
        """Exit with error when --start is after --end."""
        runner = CliRunner()
        result = runner.invoke(
            app,
            ["backtest-snipe", "--start", "2026-02-22", "--end", "2026-02-20"],
        )

        assert result.exit_code == 1
        assert "before" in result.output.lower()

    def test_successful_run_displays_results(self) -> None:
        """Successful run fetches candles and displays results."""
        runner = CliRunner()

        mock_candles = [
            _make_candle(_WINDOW_TS + 0, open_="100", close="100"),
            _make_candle(_WINDOW_TS + 60, open_="100", close="101"),
        ]

        with patch(
            "trading_tools.apps.polymarket.cli.backtest_snipe_cmd._fetch_all_candles",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = {_SYMBOL: mock_candles}

            result = runner.invoke(
                app,
                [
                    "backtest-snipe",
                    "--symbols",
                    _SYMBOL,
                    "--start",
                    "2026-02-20",
                    "--end",
                    "2026-02-22",
                    "--capital",
                    "500",
                ],
            )

        assert result.exit_code == 0
        assert "Backtest Results" in result.output
        assert "500" in result.output

    def test_verbose_flag_accepted(self) -> None:
        """Verbose flag is accepted and does not error."""
        runner = CliRunner()

        with patch(
            "trading_tools.apps.polymarket.cli.backtest_snipe_cmd._fetch_all_candles",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = {_SYMBOL: []}

            result = runner.invoke(
                app,
                [
                    "backtest-snipe",
                    "--symbols",
                    _SYMBOL,
                    "--start",
                    "2026-02-20",
                    "--end",
                    "2026-02-22",
                    "--verbose",
                ],
            )

        assert result.exit_code == 0

    def test_custom_parameters_accepted(self) -> None:
        """Custom snipe threshold, window, and scale parameters work."""
        runner = CliRunner()

        with patch(
            "trading_tools.apps.polymarket.cli.backtest_snipe_cmd._fetch_all_candles",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = {_SYMBOL: []}

            result = runner.invoke(
                app,
                [
                    "backtest-snipe",
                    "--symbols",
                    _SYMBOL,
                    "--start",
                    "2026-02-20",
                    "--end",
                    "2026-02-22",
                    "--snipe-threshold",
                    "0.85",
                    "--snipe-window",
                    "45",
                    "--scale-factor",
                    "20",
                    "--kelly-frac",
                    "0.5",
                ],
            )

        assert result.exit_code == 0
        assert "0.85" in result.output
