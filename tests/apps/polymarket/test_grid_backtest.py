"""Tests for grid backtest logic, liquidity checking, and table formatting."""

from decimal import Decimal
from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from trading_tools.apps.polymarket.backtest_common import check_order_book_liquidity
from trading_tools.apps.polymarket.cli import app
from trading_tools.apps.polymarket.grid_backtest import (
    GridBacktestResult,
    GridCell,
    format_grid_table,
    run_grid_backtest,
)
from trading_tools.apps.tick_collector.models import Tick
from trading_tools.clients.polymarket.models import OrderBook, OrderLevel
from trading_tools.core.models import ZERO, Side

_CONDITION_ID = "cond_grid_001"
_ASSET_YES = "asset_aaa"
_CAPITAL = Decimal(1000)
_KELLY_FRAC = Decimal("0.25")
_MAX_POS_PCT = Decimal("0.5")
_BUCKET_SECONDS = 1
_WINDOW_START_MS = 1_700_000_000_000
_FEE_BPS = 200


def _make_order_book(
    bids: tuple[tuple[str, str], ...] = (),
    asks: tuple[tuple[str, str], ...] = (),
) -> OrderBook:
    """Create an OrderBook for testing.

    Args:
        bids: Tuple of (price, size) pairs for the bid side.
        asks: Tuple of (price, size) pairs for the ask side.

    Returns:
        A new OrderBook instance.

    """
    bid_levels = tuple(OrderLevel(price=Decimal(p), size=Decimal(s)) for p, s in bids)
    ask_levels = tuple(OrderLevel(price=Decimal(p), size=Decimal(s)) for p, s in asks)
    spread = ZERO
    midpoint = Decimal("0.5")
    if bid_levels and ask_levels:
        spread = ask_levels[0].price - bid_levels[0].price
        midpoint = (bid_levels[0].price + ask_levels[0].price) / Decimal(2)
    return OrderBook(
        token_id="test_token",
        bids=bid_levels,
        asks=ask_levels,
        spread=spread,
        midpoint=midpoint,
    )


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


class TestCheckOrderBookLiquidity:
    """Tests for the check_order_book_liquidity helper."""

    def test_empty_book_returns_false(self) -> None:
        """Return False when the order book has no levels."""
        book = _make_order_book()
        result = check_order_book_liquidity(book, Side.BUY, Decimal("0.90"), Decimal(10))
        assert result is False

    def test_buy_yes_sufficient_asks(self) -> None:
        """Return True when asks have enough size for a BUY YES order."""
        book = _make_order_book(
            asks=(("0.88", "20"), ("0.90", "15"), ("0.92", "10")),
        )
        # price=0.90, qty=30 → asks at 0.88 (20) + 0.90 (15) = 35 >= 30
        result = check_order_book_liquidity(book, Side.BUY, Decimal("0.90"), Decimal(30))
        assert result is True

    def test_buy_yes_insufficient_asks(self) -> None:
        """Return False when asks cannot absorb the BUY YES quantity."""
        book = _make_order_book(
            asks=(("0.88", "5"), ("0.90", "3")),
        )
        # price=0.90, qty=30 → asks at 0.88 (5) + 0.90 (3) = 8 < 30
        result = check_order_book_liquidity(book, Side.BUY, Decimal("0.90"), Decimal(30))
        assert result is False

    def test_buy_no_sufficient_bids(self) -> None:
        """Return True when YES bids proxy sufficient liquidity for BUY NO."""
        book = _make_order_book(
            bids=(("0.15", "50"), ("0.12", "30")),
        )
        # SELL side → BUY NO at price=0.80
        # complement = 1 - 0.80 = 0.20
        # bids where price >= 0.20 → none (0.15, 0.12 are both < 0.20)
        result = check_order_book_liquidity(book, Side.SELL, Decimal("0.80"), Decimal(10))
        assert result is False

    def test_buy_no_with_high_bids(self) -> None:
        """Return True when YES bids are high enough for BUY NO proxy."""
        book = _make_order_book(
            bids=(("0.25", "50"), ("0.22", "30")),
        )
        # SELL side → BUY NO at price=0.80
        # complement = 1 - 0.80 = 0.20
        # bids where price >= 0.20 → 0.25 (50) + 0.22 (30) = 80 >= 10
        result = check_order_book_liquidity(book, Side.SELL, Decimal("0.80"), Decimal(10))
        assert result is True

    def test_exact_quantity_match_returns_true(self) -> None:
        """Return True when available liquidity exactly equals quantity."""
        book = _make_order_book(asks=(("0.85", "10"),))
        result = check_order_book_liquidity(book, Side.BUY, Decimal("0.85"), Decimal(10))
        assert result is True

    def test_asks_above_price_excluded(self) -> None:
        """Exclude ask levels priced above the trade price."""
        book = _make_order_book(
            asks=(("0.85", "5"), ("0.95", "100")),
        )
        # price=0.90, qty=10 → only ask at 0.85 (5) qualifies, 5 < 10
        result = check_order_book_liquidity(book, Side.BUY, Decimal("0.90"), Decimal(10))
        assert result is False


class TestRunGridBacktest:
    """Tests for the run_grid_backtest function."""

    def test_2x2_grid_returns_four_cells(self) -> None:
        """Run a 2x2 grid and verify all four cells are populated."""
        ticks = {
            _CONDITION_ID: [
                _make_tick(timestamp=_WINDOW_START_MS + 200_000, price=0.90),
                _make_tick(timestamp=_WINDOW_START_MS + 250_000, price=0.92),
            ],
        }

        thresholds = [Decimal("0.80"), Decimal("0.90")]
        windows = [60, 30]
        expected_cells = 4

        result = run_grid_backtest(
            ticks,
            book_snapshots=None,
            thresholds=thresholds,
            windows=windows,
            capital=_CAPITAL,
            kelly_frac=_KELLY_FRAC,
            max_position_pct=_MAX_POS_PCT,
            bucket_seconds=_BUCKET_SECONDS,
        )

        assert isinstance(result, GridBacktestResult)
        assert len(result.cells) == expected_cells
        assert result.thresholds == (Decimal("0.80"), Decimal("0.90"))
        assert result.windows == (60, 30)
        assert result.initial_capital == _CAPITAL
        expected_conditions = 1
        assert result.total_conditions == expected_conditions
        assert result.total_ticks > 0

    def test_cells_contain_valid_metrics(self) -> None:
        """Verify that each cell has non-negative trade counts."""
        ticks = {
            _CONDITION_ID: [
                _make_tick(timestamp=_WINDOW_START_MS + 200_000, price=0.90),
            ],
        }

        result = run_grid_backtest(
            ticks,
            book_snapshots=None,
            thresholds=[Decimal("0.85")],
            windows=[60],
            capital=_CAPITAL,
            kelly_frac=_KELLY_FRAC,
            max_position_pct=_MAX_POS_PCT,
            bucket_seconds=_BUCKET_SECONDS,
        )

        expected_cells = 1
        assert len(result.cells) == expected_cells
        cell = result.cells[0]
        assert cell.total_trades >= 0
        assert cell.wins >= 0
        assert cell.losses >= 0
        assert cell.threshold == Decimal("0.85")
        expected_window = 60
        assert cell.window_seconds == expected_window


class TestFormatGridTable:
    """Tests for the format_grid_table function."""

    def test_formats_return_pct_table(self) -> None:
        """Format a 2x2 grid as a return percentage table."""
        cells = (
            GridCell(Decimal("0.80"), 60, Decimal("5.2"), 10, 7, 3, Decimal("0.7")),
            GridCell(Decimal("0.80"), 30, Decimal("3.1"), 8, 5, 3, Decimal("0.625")),
            GridCell(Decimal("0.90"), 60, Decimal("-1.5"), 4, 1, 3, Decimal("0.25")),
            GridCell(Decimal("0.90"), 30, Decimal("0.0"), 0, 0, 0, ZERO),
        )
        result = GridBacktestResult(
            cells=cells,
            thresholds=(Decimal("0.80"), Decimal("0.90")),
            windows=(60, 30),
            initial_capital=_CAPITAL,
            total_conditions=10,
            total_ticks=5000,
        )

        table = format_grid_table(result, metric="return_pct")

        assert "Threshold" in table
        assert "60s" in table
        assert "30s" in table
        assert "0.80" in table
        assert "0.90" in table
        assert "5.2%" in table
        assert "-1.5%" in table

    def test_formats_total_trades_table(self) -> None:
        """Format a grid as a total trades table with integer values."""
        cells = (GridCell(Decimal("0.80"), 60, Decimal("5.0"), 10, 7, 3, Decimal("0.7")),)
        result = GridBacktestResult(
            cells=cells,
            thresholds=(Decimal("0.80"),),
            windows=(60,),
            initial_capital=_CAPITAL,
            total_conditions=5,
            total_ticks=1000,
        )

        table = format_grid_table(result, metric="total_trades")

        assert "10" in table

    def test_formats_win_rate_table(self) -> None:
        """Format a grid as a win rate table with percentage values."""
        cells = (GridCell(Decimal("0.80"), 60, Decimal("5.0"), 10, 7, 3, Decimal("0.7")),)
        result = GridBacktestResult(
            cells=cells,
            thresholds=(Decimal("0.80"),),
            windows=(60,),
            initial_capital=_CAPITAL,
            total_conditions=5,
            total_ticks=1000,
        )

        table = format_grid_table(result, metric="win_rate")

        assert "70.0%" in table


class TestGridBacktestCLI:
    """Tests for the grid-backtest CLI command."""

    def test_missing_dates_exits_with_error(self) -> None:
        """Exit with error when --start or --end is missing."""
        runner = CliRunner()
        result = runner.invoke(app, ["grid-backtest"])

        assert result.exit_code == 1
        assert "required" in result.output.lower()

    def test_start_after_end_exits_with_error(self) -> None:
        """Exit with error when --start is after --end."""
        runner = CliRunner()
        result = runner.invoke(
            app,
            ["grid-backtest", "--start", "2026-02-22", "--end", "2026-02-20"],
        )

        assert result.exit_code == 1
        assert "before" in result.output.lower()

    def test_no_ticks_found_shows_message(self) -> None:
        """Show a message when no ticks are found in the date range."""
        runner = CliRunner()

        with patch(
            "trading_tools.apps.polymarket.cli.grid_backtest_cmd.load_ticks",
            new_callable=AsyncMock,
        ) as mock_load:
            mock_load.return_value = ({}, {})

            result = runner.invoke(
                app,
                [
                    "grid-backtest",
                    "--start",
                    "2026-02-20",
                    "--end",
                    "2026-02-22",
                ],
            )

        assert result.exit_code == 0
        assert "no ticks" in result.output.lower()

    def test_successful_run_displays_tables(self) -> None:
        """Successful run loads ticks and displays result tables."""
        runner = CliRunner()

        mock_ticks = [
            _make_tick(timestamp=_WINDOW_START_MS + 200_000, price=0.90),
        ]

        with patch(
            "trading_tools.apps.polymarket.cli.grid_backtest_cmd.load_ticks",
            new_callable=AsyncMock,
        ) as mock_load:
            mock_load.return_value = ({_CONDITION_ID: mock_ticks}, {})

            result = runner.invoke(
                app,
                [
                    "grid-backtest",
                    "--start",
                    "2026-02-20",
                    "--end",
                    "2026-02-22",
                    "--capital",
                    "500",
                ],
            )

        assert result.exit_code == 0
        assert "Return %" in result.output
        assert "Total Trades" in result.output
        assert "Win Rate" in result.output
