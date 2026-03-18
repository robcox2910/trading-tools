"""Tests for the directional backtest runner."""

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from trading_tools.apps.directional.backtest_runner import (
    _CalibrationAccumulator,
    _determine_outcome,
    _empty_result,
    _metadata_to_opportunity,
    _snapshot_to_order_book,
    run_directional_backtest,
)
from trading_tools.apps.directional.config import DirectionalConfig
from trading_tools.apps.tick_collector.models import OrderBookSnapshot
from trading_tools.core.models import ZERO, Candle, Interval

_BASE_TS = 1_710_000_000
_WINDOW_END = _BASE_TS + 300


def _make_candle(ts: int, open_: Decimal, close: Decimal) -> Candle:
    """Create a test candle."""
    return Candle(
        symbol="BTC-USD",
        timestamp=ts,
        open=open_,
        high=max(open_, close),
        low=min(open_, close),
        close=close,
        volume=Decimal(1000),
        interval=Interval.M1,
    )


class TestDetermineOutcome:
    """Test outcome determination from candles."""

    def test_up_when_close_above_open(self) -> None:
        """Return 'Up' when close > open."""
        candles = [_make_candle(_BASE_TS, Decimal(100), Decimal(101))]
        assert _determine_outcome(candles) == "Up"

    def test_down_when_close_below_open(self) -> None:
        """Return 'Down' when close < open."""
        candles = [_make_candle(_BASE_TS, Decimal(101), Decimal(100))]
        assert _determine_outcome(candles) == "Down"

    def test_none_when_flat(self) -> None:
        """Return None when close == open."""
        candles = [_make_candle(_BASE_TS, Decimal(100), Decimal(100))]
        assert _determine_outcome(candles) is None

    def test_none_when_empty(self) -> None:
        """Return None when no candles."""
        assert _determine_outcome([]) is None


class TestMetadataToOpportunity:
    """Test market metadata conversion."""

    def test_converts_fields(self) -> None:
        """Convert metadata fields to MarketOpportunity."""
        meta = AsyncMock()
        meta.condition_id = "cond_1"
        meta.title = "BTC Up or Down?"
        meta.asset = "BTC-USD"
        meta.up_token_id = "up_tok"
        meta.down_token_id = "down_tok"
        meta.window_start_ts = _BASE_TS
        meta.window_end_ts = _WINDOW_END

        opp = _metadata_to_opportunity(meta)
        assert opp.condition_id == "cond_1"
        assert opp.asset == "BTC-USD"
        assert opp.up_price == Decimal("0.50")
        assert opp.down_price == Decimal("0.50")


class TestCalibrationAccumulator:
    """Test the calibration metric accumulator."""

    def test_record_win(self) -> None:
        """Record a winning result updates wins and pnl."""
        acc = _CalibrationAccumulator()
        result = AsyncMock()
        result.pnl = Decimal("5.00")
        result.p_up = Decimal("0.65")
        result.predicted_side = "Up"
        result.winning_side = "Up"
        acc.record(result)
        assert acc.wins == 1
        assert acc.losses == 0
        assert acc.total_pnl == Decimal("5.00")
        assert acc.brier_count == 1

    def test_record_loss(self) -> None:
        """Record a losing result updates losses and pnl."""
        acc = _CalibrationAccumulator()
        result = AsyncMock()
        result.pnl = Decimal("-3.00")
        result.p_up = Decimal("0.65")
        result.predicted_side = "Up"
        result.winning_side = "Down"
        acc.record(result)
        assert acc.wins == 0
        assert acc.losses == 1
        assert acc.total_pnl == Decimal("-3.00")


class TestEmptyResult:
    """Test empty result factory."""

    def test_returns_zero_metrics(self) -> None:
        """Empty result has zero metrics."""
        result = _empty_result(Decimal(1000))
        assert result.initial_capital == Decimal(1000)
        assert result.final_capital == Decimal(1000)
        assert result.total_pnl == ZERO
        assert result.total_trades == 0
        assert result.brier_score == ZERO


class TestSnapshotToOrderBook:
    """Test order book snapshot conversion."""

    def test_converts_json_to_order_levels(self) -> None:
        """Parse bids/asks JSON arrays into OrderLevel tuples."""
        snapshot = OrderBookSnapshot()
        snapshot.token_id = "tok_1"
        snapshot.timestamp = 1_710_000_000_000
        snapshot.bids_json = "[[0.48, 100], [0.47, 200]]"
        snapshot.asks_json = "[[0.52, 150], [0.53, 250]]"
        snapshot.spread = 0.04
        snapshot.midpoint = 0.50

        book = _snapshot_to_order_book(snapshot)
        assert book.token_id == "tok_1"
        assert len(book.bids) == 2
        assert len(book.asks) == 2
        assert book.bids[0].price == Decimal("0.48")
        assert book.bids[0].size == Decimal(100)
        assert book.asks[0].price == Decimal("0.52")
        assert book.spread == Decimal("0.04")
        assert book.midpoint == Decimal("0.5")

    def test_handles_empty_levels(self) -> None:
        """Handle empty bid/ask arrays."""
        snapshot = OrderBookSnapshot()
        snapshot.token_id = "tok_2"
        snapshot.timestamp = 1_710_000_000_000
        snapshot.bids_json = "[]"
        snapshot.asks_json = "[]"
        snapshot.spread = 0.0
        snapshot.midpoint = 0.50

        book = _snapshot_to_order_book(snapshot)
        assert len(book.bids) == 0
        assert len(book.asks) == 0


class TestRunDirectionalBacktest:
    """Test the full backtest runner with mocked repository."""

    @pytest.mark.asyncio
    async def test_no_metadata_returns_empty(self) -> None:
        """Return empty result when no market metadata found."""
        config = DirectionalConfig()
        repo = AsyncMock()
        repo.get_market_metadata_in_range = AsyncMock(return_value=[])

        result = await run_directional_backtest(
            config=config,
            repo=repo,
            start_ts=_BASE_TS,
            end_ts=_WINDOW_END,
        )
        assert result.total_windows == 0
        assert result.total_pnl == ZERO

    @pytest.mark.asyncio
    async def test_skips_windows_without_candles(self) -> None:
        """Skip windows when no candles are available (no outcome)."""
        config = DirectionalConfig()
        meta = AsyncMock()
        meta.condition_id = "cond_1"
        meta.title = "BTC Up or Down?"
        meta.asset = "BTC-USD"
        meta.up_token_id = "up_tok"
        meta.down_token_id = "down_tok"
        meta.window_start_ts = _BASE_TS
        meta.window_end_ts = _WINDOW_END

        repo = AsyncMock()
        repo.get_market_metadata_in_range = AsyncMock(return_value=[meta])
        repo.get_nearest_book_snapshot = AsyncMock(return_value=None)

        result = await run_directional_backtest(
            config=config,
            repo=repo,
            start_ts=_BASE_TS,
            end_ts=_WINDOW_END,
            candles_by_asset={},
        )
        assert result.total_windows == 1
        assert result.skipped == 1
        assert result.total_trades == 0

    @pytest.mark.asyncio
    async def test_loads_whale_signal_when_repo_provided(self) -> None:
        """Pass whale signal to replay adapter when whale_repo is given."""
        config = DirectionalConfig()
        meta = AsyncMock()
        meta.condition_id = "cond_1"
        meta.title = "BTC Up or Down?"
        meta.asset = "BTC-USD"
        meta.up_token_id = "up_tok"
        meta.down_token_id = "down_tok"
        meta.window_start_ts = _BASE_TS
        meta.window_end_ts = _WINDOW_END

        repo = AsyncMock()
        repo.get_market_metadata_in_range = AsyncMock(return_value=[meta])
        repo.get_nearest_book_snapshot = AsyncMock(return_value=None)

        whale_repo = AsyncMock()
        whale_repo.get_whale_signal = AsyncMock(return_value="Up")

        # Provide candles so the window doesn't skip due to no outcome
        candles = [_make_candle(_BASE_TS, Decimal(100), Decimal(101))]

        result = await run_directional_backtest(
            config=config,
            repo=repo,
            start_ts=_BASE_TS,
            end_ts=_WINDOW_END,
            candles_by_asset={"BTC-USD": candles},
            whale_repo=whale_repo,
        )
        whale_repo.get_whale_signal.assert_called_once_with("cond_1")
        # Window was processed (not skipped due to missing candles)
        assert result.total_windows == 1
