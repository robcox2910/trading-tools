"""Tests for the limit order spread capture backtester."""

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from trading_tools.apps.spread_capture.limit_backtest import (
    LimitBacktestConfig,
    LimitBacktestResult,
    LimitGridResult,
    WindowFillResult,
    _aggregate_results,
    _available_qty_at_or_below,
    _parse_asks,
    format_limit_grid_table,
    run_limit_backtest,
    run_limit_grid,
    simulate_limit_fills,
)
from trading_tools.clients.polymarket.models import OrderLevel
from trading_tools.core.models import ZERO

_WINDOW_START = 1_710_000_000
_WINDOW_END = 1_710_000_300


class _FakeSnapshot:
    """Fake OrderBookSnapshot for testing without a real DB."""

    def __init__(
        self,
        token_id: str = "up_tok",
        timestamp: int = 1_710_000_150_000,
        asks_json: str = '[["0.15", "100"]]',
        bids_json: str = '[["0.10", "50"]]',
    ) -> None:
        """Initialize fake snapshot.

        Args:
            token_id: Token identifier.
            timestamp: Epoch milliseconds.
            asks_json: JSON-serialised ask levels.
            bids_json: JSON-serialised bid levels.

        """
        self.token_id = token_id
        self.timestamp = timestamp
        self.asks_json = asks_json
        self.bids_json = bids_json
        self.spread = 0.05
        self.midpoint = 0.125


class _FakeMetadata:
    """Fake MarketMetadata for testing without a real DB."""

    def __init__(
        self,
        condition_id: str = "cond_1",
        asset: str = "BTC-USD",
        title: str = "BTC Up or Down?",
        up_token_id: str = "up_tok",
        down_token_id: str = "down_tok",
        window_start_ts: int = _WINDOW_START,
        window_end_ts: int = _WINDOW_END,
        series_slug: str | None = None,
    ) -> None:
        """Initialize fake metadata.

        Args:
            condition_id: Market condition ID.
            asset: Asset symbol.
            title: Market title.
            up_token_id: Up token ID.
            down_token_id: Down token ID.
            window_start_ts: Window start timestamp.
            window_end_ts: Window end timestamp.
            series_slug: Optional series slug.

        """
        self.condition_id = condition_id
        self.asset = asset
        self.title = title
        self.up_token_id = up_token_id
        self.down_token_id = down_token_id
        self.window_start_ts = window_start_ts
        self.window_end_ts = window_end_ts
        self.series_slug = series_slug


class TestParseAsks:
    """Test the _parse_asks helper."""

    def test_parses_ask_levels(self) -> None:
        """Parse JSON asks into sorted OrderLevel tuples."""
        snap = _FakeSnapshot(asks_json='[["0.20", "50"], ["0.10", "30"]]')
        asks = _parse_asks(snap)  # type: ignore[arg-type]
        assert len(asks) == 2
        assert asks[0].price == Decimal("0.10")
        assert asks[1].price == Decimal("0.20")

    def test_empty_asks(self) -> None:
        """Return empty tuple for empty asks."""
        snap = _FakeSnapshot(asks_json="[]")
        asks = _parse_asks(snap)  # type: ignore[arg-type]
        assert asks == ()


class TestAvailableQtyAtOrBelow:
    """Test the _available_qty_at_or_below helper."""

    def test_sums_levels_at_or_below(self) -> None:
        """Sum all ask sizes at or below the bid price."""
        asks = (
            OrderLevel(price=Decimal("0.10"), size=Decimal(30)),
            OrderLevel(price=Decimal("0.15"), size=Decimal(20)),
            OrderLevel(price=Decimal("0.25"), size=Decimal(50)),
        )
        qty = _available_qty_at_or_below(asks, Decimal("0.15"))
        assert qty == Decimal(50)

    def test_no_levels_below(self) -> None:
        """Return zero when no asks are at or below bid."""
        asks = (OrderLevel(price=Decimal("0.30"), size=Decimal(100)),)
        qty = _available_qty_at_or_below(asks, Decimal("0.20"))
        assert qty == ZERO

    def test_empty_asks(self) -> None:
        """Return zero for empty ask list."""
        qty = _available_qty_at_or_below((), Decimal("0.50"))
        assert qty == ZERO


class TestSimulateLimitFills:
    """Test the core fill simulation logic."""

    def _make_config(
        self,
        bid_up: str = "0.15",
        bid_down: str = "0.15",
        order_size: str = "50",
        entry_delay: str = "0.0",
    ) -> LimitBacktestConfig:
        """Create a test config.

        Args:
            bid_up: Up bid price.
            bid_down: Down bid price.
            order_size: Order size in tokens.
            entry_delay: Entry delay fraction.

        Returns:
            A ``LimitBacktestConfig`` for testing.

        """
        return LimitBacktestConfig(
            bid_price_up=Decimal(bid_up),
            bid_price_down=Decimal(bid_down),
            order_size=Decimal(order_size),
            entry_delay_pct=Decimal(entry_delay),
        )

    def test_both_sides_fill(self) -> None:
        """Both sides fill when asks exist at or below bid prices."""
        config = self._make_config(bid_up="0.20", bid_down="0.20")
        up_snaps = [
            _FakeSnapshot(token_id="up", asks_json='[["0.15", "100"]]'),
        ]
        down_snaps = [
            _FakeSnapshot(token_id="down", asks_json='[["0.18", "100"]]'),
        ]
        result = simulate_limit_fills(
            config,
            up_snaps,  # type: ignore[arg-type]
            down_snaps,  # type: ignore[arg-type]
            _WINDOW_START,
            _WINDOW_END,
            "Up",
        )
        assert result.up_filled is True
        assert result.down_filled is True
        assert result.paired_qty == Decimal(50)

    def test_neither_fills_when_asks_above_bid(self) -> None:
        """Neither side fills when all asks are above bid prices."""
        config = self._make_config(bid_up="0.05", bid_down="0.05")
        up_snaps = [
            _FakeSnapshot(token_id="up", asks_json='[["0.15", "100"]]'),
        ]
        down_snaps = [
            _FakeSnapshot(token_id="down", asks_json='[["0.20", "100"]]'),
        ]
        result = simulate_limit_fills(
            config,
            up_snaps,  # type: ignore[arg-type]
            down_snaps,  # type: ignore[arg-type]
            _WINDOW_START,
            _WINDOW_END,
            None,
        )
        assert result.up_filled is False
        assert result.down_filled is False
        assert result.pnl == ZERO

    def test_up_only_fills(self) -> None:
        """Only Up fills when Down asks are too expensive."""
        config = self._make_config(bid_up="0.20", bid_down="0.05")
        up_snaps = [_FakeSnapshot(asks_json='[["0.15", "100"]]')]
        down_snaps = [_FakeSnapshot(asks_json='[["0.20", "100"]]')]
        result = simulate_limit_fills(
            config,
            up_snaps,  # type: ignore[arg-type]
            down_snaps,  # type: ignore[arg-type]
            _WINDOW_START,
            _WINDOW_END,
            "Up",
        )
        assert result.up_filled is True
        assert result.down_filled is False
        assert result.up_fill_qty == Decimal(50)
        assert result.down_fill_qty == ZERO

    def test_fill_qty_clamped_by_depth(self) -> None:
        """Fill quantity is clamped to available depth."""
        config = self._make_config(bid_up="0.20", order_size="200")
        up_snaps = [_FakeSnapshot(asks_json='[["0.15", "30"]]')]
        down_snaps = [_FakeSnapshot(asks_json='[["0.15", "100"]]')]
        result = simulate_limit_fills(
            config,
            up_snaps,  # type: ignore[arg-type]
            down_snaps,  # type: ignore[arg-type]
            _WINDOW_START,
            _WINDOW_END,
            None,
        )
        assert result.up_fill_qty == Decimal(30)

    def test_entry_delay_skips_early_snapshots(self) -> None:
        """Snapshots before entry delay are skipped."""
        config = self._make_config(entry_delay="0.50")
        # Snapshot at window start (should be skipped)
        early_ms = _WINDOW_START * 1000
        # Snapshot after 50% of window
        late_ms = (_WINDOW_START + 150) * 1000 + 1
        early_snap = _FakeSnapshot(
            timestamp=early_ms,
            asks_json='[["0.10", "100"]]',
        )
        late_snap = _FakeSnapshot(
            timestamp=late_ms,
            asks_json='[["0.50", "100"]]',
        )
        result = simulate_limit_fills(
            config,
            [early_snap, late_snap],  # type: ignore[arg-type]
            [],  # type: ignore[arg-type]
            _WINDOW_START,
            _WINDOW_END,
            None,
        )
        # Only late snap visible, but its ask (0.50) is above bid (0.15)
        assert result.up_filled is False

    def test_guaranteed_pnl_positive_when_combined_under_one(self) -> None:
        """Guaranteed P&L is positive when combined cost < $1.00."""
        config = self._make_config(
            bid_up="0.20",
            bid_down="0.20",
            order_size="10",
        )
        up_snaps = [_FakeSnapshot(asks_json='[["0.20", "100"]]')]
        down_snaps = [_FakeSnapshot(asks_json='[["0.20", "100"]]')]
        result = simulate_limit_fills(
            config,
            up_snaps,  # type: ignore[arg-type]
            down_snaps,  # type: ignore[arg-type]
            _WINDOW_START,
            _WINDOW_END,
            "Up",
        )
        assert result.combined_cost == Decimal("0.40")
        assert result.guaranteed_pnl > ZERO

    def test_empty_snapshots_returns_no_fills(self) -> None:
        """Return no fills when snapshot lists are empty."""
        config = self._make_config()
        result = simulate_limit_fills(
            config,
            [],
            [],
            _WINDOW_START,
            _WINDOW_END,
            None,
        )
        assert result.up_filled is False
        assert result.down_filled is False
        assert result.pnl == ZERO


class TestAggregateResults:
    """Test the _aggregate_results helper."""

    def test_counts_fill_categories(self) -> None:
        """Correctly count both/up-only/down-only/neither fills."""
        results = [
            WindowFillResult(
                condition_id="a",
                asset="BTC",
                up_filled=True,
                down_filled=True,
                up_fill_qty=Decimal(10),
                down_fill_qty=Decimal(10),
                combined_cost=Decimal("0.40"),
                paired_qty=Decimal(10),
                guaranteed_pnl=Decimal(5),
                pnl=Decimal(5),
                outcome="Up",
            ),
            WindowFillResult(
                condition_id="b",
                asset="BTC",
                up_filled=True,
                down_filled=False,
                up_fill_qty=Decimal(10),
                down_fill_qty=ZERO,
                combined_cost=Decimal("0.40"),
                paired_qty=ZERO,
                guaranteed_pnl=ZERO,
                pnl=Decimal(-2),
                outcome="Down",
            ),
            WindowFillResult(
                condition_id="c",
                asset="BTC",
                up_filled=False,
                down_filled=False,
                up_fill_qty=ZERO,
                down_fill_qty=ZERO,
                combined_cost=Decimal("0.40"),
                paired_qty=ZERO,
                guaranteed_pnl=ZERO,
                pnl=ZERO,
                outcome=None,
            ),
        ]
        config = LimitBacktestConfig(
            bid_price_up=Decimal("0.20"),
            bid_price_down=Decimal("0.20"),
            order_size=Decimal(10),
        )
        agg = _aggregate_results(config, results, total_windows=3)
        assert agg.both_filled == 1
        assert agg.up_only == 1
        assert agg.down_only == 0
        assert agg.neither_filled == 1
        assert agg.total_pnl == Decimal(3)

    def test_empty_results(self) -> None:
        """Return zeros for empty result list."""
        config = LimitBacktestConfig(
            bid_price_up=Decimal("0.20"),
            bid_price_down=Decimal("0.20"),
            order_size=Decimal(10),
        )
        agg = _aggregate_results(config, [], total_windows=0)
        assert agg.total_windows == 0
        assert agg.total_pnl == ZERO


class TestRunLimitBacktest:
    """Test the single-config backtest runner."""

    @pytest.mark.asyncio
    async def test_no_metadata_returns_empty(self) -> None:
        """Return empty result when no market metadata exists."""
        repo = AsyncMock()
        repo.get_market_metadata_in_range = AsyncMock(return_value=[])
        config = LimitBacktestConfig(
            bid_price_up=Decimal("0.20"),
            bid_price_down=Decimal("0.20"),
            order_size=Decimal(10),
        )
        result = await run_limit_backtest(config, repo, 0, 100)
        assert result.total_windows == 0
        assert result.total_pnl == ZERO

    @pytest.mark.asyncio
    async def test_runs_with_pre_fetched_snapshots(self) -> None:
        """Run backtest using pre-fetched snapshots instead of DB queries."""
        meta = _FakeMetadata()
        repo = AsyncMock()
        repo.get_market_metadata_in_range = AsyncMock(return_value=[meta])

        up_snap = _FakeSnapshot(
            token_id="up_tok",
            timestamp=_WINDOW_START * 1000 + 50_000,
            asks_json='[["0.15", "100"]]',
        )
        down_snap = _FakeSnapshot(
            token_id="down_tok",
            timestamp=_WINDOW_START * 1000 + 50_000,
            asks_json='[["0.15", "100"]]',
        )
        config = LimitBacktestConfig(
            bid_price_up=Decimal("0.20"),
            bid_price_down=Decimal("0.20"),
            order_size=Decimal(10),
        )
        result = await run_limit_backtest(
            config,
            repo,
            _WINDOW_START,
            _WINDOW_END,
            all_snapshots=[up_snap, down_snap],  # type: ignore[list-item]
        )
        assert result.total_windows == 1
        assert result.both_filled == 1
        assert result.total_pnl > ZERO


class TestRunLimitGrid:
    """Test the grid sweep runner."""

    @pytest.mark.asyncio
    async def test_grid_produces_correct_cell_count(self) -> None:
        """Grid generates one result per parameter combination."""
        meta = _FakeMetadata()
        repo = AsyncMock()
        repo.get_market_metadata_in_range = AsyncMock(return_value=[meta])
        repo.get_all_book_snapshots_in_range = AsyncMock(
            return_value=[
                _FakeSnapshot(
                    token_id="up_tok",
                    timestamp=_WINDOW_START * 1000 + 50_000,
                    asks_json='[["0.15", "100"]]',
                ),
                _FakeSnapshot(
                    token_id="down_tok",
                    timestamp=_WINDOW_START * 1000 + 50_000,
                    asks_json='[["0.15", "100"]]',
                ),
            ]
        )

        result = await run_limit_grid(
            repo=repo,
            start_ts=_WINDOW_START,
            end_ts=_WINDOW_END,
            bid_prices_up=[Decimal("0.10"), Decimal("0.20")],
            bid_prices_down=[Decimal("0.10"), Decimal("0.20")],
            order_sizes=[Decimal(10)],
            entry_delays=[ZERO],
        )
        expected_cells = 2 * 2 * 1 * 1
        assert len(result.cells) == expected_cells
        assert result.total_windows == 1


class TestFormatLimitGridTable:
    """Test the markdown table formatter."""

    def test_format_total_pnl_table(self) -> None:
        """Format a simple 2x2 grid as markdown."""
        cells: list[LimitBacktestResult] = []
        for bu in [Decimal("0.10"), Decimal("0.20")]:
            for bd in [Decimal("0.10"), Decimal("0.20")]:
                config = LimitBacktestConfig(
                    bid_price_up=bu,
                    bid_price_down=bd,
                    order_size=Decimal(10),
                )
                cells.append(
                    LimitBacktestResult(
                        config=config,
                        total_windows=10,
                        both_filled=5,
                        up_only=2,
                        down_only=2,
                        neither_filled=1,
                        fill_rate_both=Decimal("0.50"),
                        avg_guaranteed_pnl=Decimal("1.00"),
                        total_pnl=Decimal("5.00"),
                        avg_pnl=Decimal("0.50"),
                        std_pnl=Decimal("0.25"),
                        sharpe=Decimal("2.0"),
                    )
                )

        result = LimitGridResult(
            cells=tuple(cells),
            bid_prices_up=(Decimal("0.10"), Decimal("0.20")),
            bid_prices_down=(Decimal("0.10"), Decimal("0.20")),
            order_sizes=(Decimal(10),),
            entry_delays=(ZERO,),
            total_windows=10,
        )
        table = format_limit_grid_table(result, metric="total_pnl")
        assert "Bid Up \\ Down" in table
        assert "$5.00" in table
        assert "0.10" in table
        assert "0.20" in table

    def test_format_fill_rate_table(self) -> None:
        """Fill rate metric shows percentage values."""
        config = LimitBacktestConfig(
            bid_price_up=Decimal("0.10"),
            bid_price_down=Decimal("0.10"),
            order_size=Decimal(10),
        )
        cells = [
            LimitBacktestResult(
                config=config,
                total_windows=10,
                both_filled=7,
                up_only=1,
                down_only=1,
                neither_filled=1,
                fill_rate_both=Decimal("0.70"),
                avg_guaranteed_pnl=Decimal("1.00"),
                total_pnl=Decimal("5.00"),
                avg_pnl=Decimal("0.50"),
                std_pnl=Decimal("0.25"),
                sharpe=Decimal("2.0"),
            )
        ]
        result = LimitGridResult(
            cells=tuple(cells),
            bid_prices_up=(Decimal("0.10"),),
            bid_prices_down=(Decimal("0.10"),),
            order_sizes=(Decimal(10),),
            entry_delays=(ZERO,),
            total_windows=10,
        )
        table = format_limit_grid_table(result, metric="fill_rate_both")
        assert "70.0%" in table
