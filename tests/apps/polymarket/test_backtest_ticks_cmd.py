"""Tests for the backtest-ticks CLI command."""

from decimal import Decimal
from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from trading_tools.apps.polymarket.cli import app
from trading_tools.apps.polymarket.cli.backtest_ticks_cmd import TickBacktestRunner
from trading_tools.apps.polymarket_bot.models import PaperTradingResult
from trading_tools.apps.polymarket_bot.portfolio import PaperPortfolio
from trading_tools.apps.polymarket_bot.strategies.late_snipe import PMLateSnipeStrategy
from trading_tools.apps.tick_collector.models import Tick
from trading_tools.apps.tick_collector.snapshot_builder import SnapshotBuilder

_CONDITION_ID = "cond_test_abc"
_ASSET_YES = "asset_aaa"
_ASSET_NO = "asset_zzz"
_FEE_BPS = 200
_THRESHOLD = Decimal("0.80")
_WINDOW_SECONDS = 300
_KELLY_FRAC = Decimal("0.25")
_CAPITAL = Decimal(1000)
_MAX_POS_PCT = Decimal("0.5")
_FIVE_MINUTES_MS = 300_000
_BUCKET_SECONDS = 1
_WINDOW_START_MS = 1_700_000_000_000


def _make_tick(
    asset_id: str = _ASSET_YES,
    condition_id: str = _CONDITION_ID,
    timestamp: int = _WINDOW_START_MS,
    price: float = 0.72,
) -> Tick:
    """Create a Tick instance for testing.

    Args:
        asset_id: Token identifier.
        condition_id: Market condition identifier.
        timestamp: Epoch milliseconds.
        price: Trade price.

    Returns:
        A new Tick instance.

    """
    return Tick(
        asset_id=asset_id,
        condition_id=condition_id,
        price=price,
        size=10.0,
        side="BUY",
        fee_rate_bps=_FEE_BPS,
        timestamp=timestamp,
        received_at=timestamp + 50,
    )


class TestTickBacktestRunner:
    """Tests for the TickBacktestRunner replay logic."""

    def test_replay_with_no_windows_returns_zero_trades(self) -> None:
        """Replay with no windows produces zero trades."""
        strategy = PMLateSnipeStrategy(threshold=_THRESHOLD, window_seconds=_WINDOW_SECONDS)
        portfolio = PaperPortfolio(_CAPITAL, _MAX_POS_PCT)

        runner = TickBacktestRunner(
            strategy=strategy,
            portfolio=portfolio,
            kelly_frac=_KELLY_FRAC,
            initial_capital=_CAPITAL,
        )
        result = runner.replay([])

        assert result.snapshots_processed == 0
        assert len(result.trades) == 0

    def test_replay_processes_windows(self) -> None:
        """Replay with tick windows processes snapshots."""
        strategy = PMLateSnipeStrategy(threshold=_THRESHOLD, window_seconds=_WINDOW_SECONDS)
        portfolio = PaperPortfolio(_CAPITAL, _MAX_POS_PCT)
        builder = SnapshotBuilder(bucket_seconds=_BUCKET_SECONDS)

        # Create ticks that show high YES price (should trigger snipe)
        ticks = [
            _make_tick(timestamp=_WINDOW_START_MS + 200_000, price=0.90),
            _make_tick(timestamp=_WINDOW_START_MS + 250_000, price=0.92),
        ]

        window = builder.detect_window(_CONDITION_ID, ticks)
        snapshots = builder.build_snapshots(ticks, window)

        runner = TickBacktestRunner(
            strategy=strategy,
            portfolio=portfolio,
            kelly_frac=_KELLY_FRAC,
            initial_capital=_CAPITAL,
        )
        result = runner.replay([(window, snapshots)])

        expected_windows = 1
        assert result.metrics["windows_processed"] == Decimal(expected_windows)
        assert result.snapshots_processed > 0

    def test_replay_returns_paper_trading_result(self) -> None:
        """Replay returns a PaperTradingResult with correct type and capital."""
        strategy = PMLateSnipeStrategy(threshold=_THRESHOLD, window_seconds=_WINDOW_SECONDS)
        portfolio = PaperPortfolio(_CAPITAL, _MAX_POS_PCT)

        runner = TickBacktestRunner(
            strategy=strategy,
            portfolio=portfolio,
            kelly_frac=_KELLY_FRAC,
            initial_capital=_CAPITAL,
        )
        result = runner.replay([])

        assert isinstance(result, PaperTradingResult)
        assert result.initial_capital == _CAPITAL
        assert result.strategy_name.startswith("pm_late_snipe")


class TestBacktestTicksCLI:
    """Tests for the backtest-ticks CLI command."""

    def test_missing_dates_exits_with_error(self) -> None:
        """Exit with error when --start or --end is missing."""
        runner = CliRunner()
        result = runner.invoke(app, ["backtest-ticks"])

        assert result.exit_code == 1
        assert "required" in result.output.lower()

    def test_start_after_end_exits_with_error(self) -> None:
        """Exit with error when --start is after --end."""
        runner = CliRunner()
        result = runner.invoke(
            app,
            ["backtest-ticks", "--start", "2026-02-22", "--end", "2026-02-20"],
        )

        assert result.exit_code == 1
        assert "before" in result.output.lower()

    def test_successful_run_displays_results(self) -> None:
        """Successful run loads ticks and displays results."""
        runner = CliRunner()

        mock_ticks = [
            _make_tick(timestamp=_WINDOW_START_MS + 200_000, price=0.90),
        ]

        with (
            patch(
                "trading_tools.apps.polymarket.cli.backtest_ticks_cmd._load_ticks",
                new_callable=AsyncMock,
            ) as mock_load,
        ):
            mock_load.return_value = {_CONDITION_ID: mock_ticks}

            result = runner.invoke(
                app,
                [
                    "backtest-ticks",
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
            "trading_tools.apps.polymarket.cli.backtest_ticks_cmd._load_ticks",
            new_callable=AsyncMock,
        ) as mock_load:
            mock_load.return_value = {}

            result = runner.invoke(
                app,
                [
                    "backtest-ticks",
                    "--start",
                    "2026-02-20",
                    "--end",
                    "2026-02-22",
                    "--verbose",
                ],
            )

        assert result.exit_code == 0

    def test_no_ticks_found_shows_message(self) -> None:
        """Show a message when no ticks are found in the date range."""
        runner = CliRunner()

        with patch(
            "trading_tools.apps.polymarket.cli.backtest_ticks_cmd._load_ticks",
            new_callable=AsyncMock,
        ) as mock_load:
            mock_load.return_value = {}

            result = runner.invoke(
                app,
                [
                    "backtest-ticks",
                    "--start",
                    "2026-02-20",
                    "--end",
                    "2026-02-22",
                ],
            )

        assert result.exit_code == 0
        assert "no ticks" in result.output.lower() or "0" in result.output
