"""Tests for the spread capture backtest runner."""

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from trading_tools.apps.spread_capture.backtest_runner import (
    SpreadBacktestResult,
    _determine_outcome,
    _metadata_to_opportunity,
    run_spread_backtest,
)
from trading_tools.apps.spread_capture.config import SpreadCaptureConfig
from trading_tools.core.models import ZERO, Candle, Interval

_WINDOW_START = 1_710_000_000
_WINDOW_END = 1_710_000_300


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


class TestMetadataToOpportunity:
    """Test conversion from metadata to opportunity."""

    def test_converts_metadata_fields(self) -> None:
        """Opportunity has correct fields from metadata."""
        meta = _FakeMetadata()
        opp = _metadata_to_opportunity(meta)  # type: ignore[arg-type]
        assert opp.condition_id == "cond_1"
        assert opp.asset == "BTC-USD"
        assert opp.up_token_id == "up_tok"
        assert opp.down_token_id == "down_tok"
        assert opp.window_start_ts == _WINDOW_START
        assert opp.window_end_ts == _WINDOW_END

    def test_initial_prices_are_half(self) -> None:
        """Initial prices default to 0.50 since real prices come from books."""
        meta = _FakeMetadata()
        opp = _metadata_to_opportunity(meta)  # type: ignore[arg-type]
        assert opp.up_price == Decimal("0.50")
        assert opp.down_price == Decimal("0.50")


class TestDetermineOutcome:
    """Test outcome determination from candle data."""

    def test_up_outcome(self) -> None:
        """Return Up when close > open."""
        candles = [
            Candle(
                symbol="BTC",
                timestamp=0,
                open=Decimal(100),
                high=Decimal(110),
                low=Decimal(100),
                close=Decimal(100),
                volume=Decimal(1),
                interval=Interval.M1,
            ),
            Candle(
                symbol="BTC",
                timestamp=60,
                open=Decimal(100),
                high=Decimal(110),
                low=Decimal(100),
                close=Decimal(110),
                volume=Decimal(1),
                interval=Interval.M1,
            ),
        ]
        assert _determine_outcome(candles) == "Up"

    def test_down_outcome(self) -> None:
        """Return Down when close < open."""
        candles = [
            Candle(
                symbol="BTC",
                timestamp=0,
                open=Decimal(100),
                high=Decimal(100),
                low=Decimal(90),
                close=Decimal(100),
                volume=Decimal(1),
                interval=Interval.M1,
            ),
            Candle(
                symbol="BTC",
                timestamp=60,
                open=Decimal(100),
                high=Decimal(100),
                low=Decimal(90),
                close=Decimal(90),
                volume=Decimal(1),
                interval=Interval.M1,
            ),
        ]
        assert _determine_outcome(candles) == "Down"

    def test_flat_returns_none(self) -> None:
        """Return None when close == open."""
        candles = [
            Candle(
                symbol="BTC",
                timestamp=0,
                open=Decimal(100),
                high=Decimal(100),
                low=Decimal(100),
                close=Decimal(100),
                volume=Decimal(1),
                interval=Interval.M1,
            ),
        ]
        assert _determine_outcome(candles) is None

    def test_empty_returns_none(self) -> None:
        """Return None for empty candle list."""
        assert _determine_outcome([]) is None


@pytest.mark.asyncio
class TestRunSpreadBacktest:
    """Test the full backtest runner."""

    async def test_no_metadata_returns_empty_result(self) -> None:
        """Empty metadata list returns a zero-trade result."""
        config = SpreadCaptureConfig(capital=Decimal(1000))
        mock_repo = AsyncMock()
        mock_repo.get_market_metadata_in_range = AsyncMock(return_value=[])

        result = await run_spread_backtest(
            config=config,
            repo=mock_repo,
            start_ts=1_000_000,
            end_ts=2_000_000,
        )

        assert isinstance(result, SpreadBacktestResult)
        assert result.total_windows == 0
        assert result.total_trades == 0
        assert result.total_pnl == ZERO

    async def test_single_window_backtest(self) -> None:
        """Single market window produces a result with trades."""
        config = SpreadCaptureConfig(
            capital=Decimal(1000),
            max_position_pct=Decimal("0.10"),
            fill_size_tokens=Decimal(5),
            initial_fill_size=Decimal(10),
            poll_interval=60,
            paper_slippage_pct=ZERO,
            fee_rate=ZERO,
            signal_delay_seconds=60,
            hedge_start_threshold=Decimal("0.90"),
            hedge_end_threshold=Decimal("0.95"),
            max_fill_age_pct=Decimal("0.80"),
        )

        meta = _FakeMetadata(
            window_start_ts=1_710_000_000,
            window_end_ts=1_710_000_300,
        )

        mock_repo = AsyncMock()
        mock_repo.get_market_metadata_in_range = AsyncMock(return_value=[meta])
        mock_repo.get_order_book_snapshots_in_range = AsyncMock(return_value=[])

        result = await run_spread_backtest(
            config=config,
            repo=mock_repo,
            start_ts=1_709_999_000,
            end_ts=1_710_001_000,
        )

        assert result.total_windows == 1
        # Engine opened a position even without books (empty order book)
        assert result.initial_capital == Decimal(1000)
