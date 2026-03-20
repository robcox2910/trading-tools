"""Tests for the LiveMarketData adapter."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from trading_tools.apps.directional.market_data_live import LiveMarketData
from trading_tools.apps.directional.models import MarketOpportunity
from trading_tools.apps.spread_capture.models import SpreadOpportunity
from trading_tools.clients.polymarket.models import OrderBook, OrderLevel
from trading_tools.core.models import Candle, Interval

_HALF = Decimal("0.50")
_BASE_TS = 1_710_000_000


def _make_spread_opp(condition_id: str = "cond_1") -> SpreadOpportunity:
    """Create a SpreadOpportunity for scanner mock returns."""
    return SpreadOpportunity(
        condition_id=condition_id,
        title="BTC Up or Down?",
        asset="BTC-USD",
        up_token_id="up_tok",
        down_token_id="down_tok",
        up_price=_HALF,
        down_price=_HALF,
        combined=Decimal(1),
        margin=Decimal(0),
        window_start_ts=_BASE_TS,
        window_end_ts=_BASE_TS + 300,
        up_ask_depth=Decimal(100),
        down_ask_depth=Decimal(100),
    )


def _make_order_book(token_id: str) -> OrderBook:
    """Create a test OrderBook."""
    return OrderBook(
        token_id=token_id,
        bids=(OrderLevel(price=_HALF, size=Decimal(100)),),
        asks=(OrderLevel(price=_HALF, size=Decimal(100)),),
        spread=Decimal("0.01"),
        midpoint=_HALF,
        min_order_size=Decimal(5),
    )


def _make_candle(ts: int) -> Candle:
    """Create a test candle."""
    return Candle(
        symbol="BTC-USD",
        timestamp=ts,
        open=Decimal(100),
        high=Decimal(101),
        low=Decimal(99),
        close=Decimal(100),
        volume=Decimal(1000),
        interval=Interval.M1,
    )


class TestLiveMarketData:
    """Test the live market data adapter with mocked dependencies."""

    @pytest.mark.asyncio
    async def test_get_active_markets_converts_opportunities(self) -> None:
        """Convert SpreadOpportunity to MarketOpportunity."""
        client = MagicMock()
        candle_provider = MagicMock()

        adapter = LiveMarketData.__new__(LiveMarketData)
        adapter._client = client
        adapter._candle_provider = candle_provider
        adapter._book_feed = None

        scanner = AsyncMock()
        scanner.scan_per_side = AsyncMock(return_value=[_make_spread_opp()])
        adapter._scanner = scanner

        markets = await adapter.get_active_markets(set())
        assert len(markets) == 1
        assert markets[0].condition_id == "cond_1"
        assert markets[0].asset == "BTC-USD"

    @pytest.mark.asyncio
    async def test_get_order_books(self) -> None:
        """Fetch order books via client."""
        client = AsyncMock()
        up_book = _make_order_book("up_tok")
        down_book = _make_order_book("down_tok")
        client.get_order_book = AsyncMock(side_effect=[up_book, down_book])

        adapter = LiveMarketData.__new__(LiveMarketData)
        adapter._client = client
        adapter._book_feed = None

        result = await adapter.get_order_books("up_tok", "down_tok")
        assert result == (up_book, down_book)

    @pytest.mark.asyncio
    async def test_get_binance_candles(self) -> None:
        """Fetch candles via provider."""
        candle_provider = AsyncMock()
        candles = [_make_candle(_BASE_TS)]
        candle_provider.get_candles = AsyncMock(return_value=candles)

        adapter = LiveMarketData.__new__(LiveMarketData)
        adapter._candle_provider = candle_provider

        result = await adapter.get_binance_candles("BTC-USD", _BASE_TS, _BASE_TS + 60)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_resolve_outcome_up(self) -> None:
        """Resolve outcome as Up when close > open."""
        candle_provider = AsyncMock()
        open_candle = Candle(
            symbol="BTC-USD",
            timestamp=_BASE_TS,
            open=Decimal(100),
            high=Decimal(101),
            low=Decimal(99),
            close=Decimal(100),
            volume=Decimal(1000),
            interval=Interval.M1,
        )
        close_candle = Candle(
            symbol="BTC-USD",
            timestamp=_BASE_TS + 240,
            open=Decimal(100),
            high=Decimal(102),
            low=Decimal(100),
            close=Decimal(102),
            volume=Decimal(1000),
            interval=Interval.M1,
        )
        candle_provider.get_candles = AsyncMock(return_value=[open_candle, close_candle])

        adapter = LiveMarketData.__new__(LiveMarketData)
        adapter._candle_provider = candle_provider

        opp = MarketOpportunity(
            condition_id="cond_1",
            title="BTC",
            asset="BTC-USD",
            up_token_id="up",
            down_token_id="down",
            window_start_ts=_BASE_TS,
            window_end_ts=_BASE_TS + 300,
            up_price=_HALF,
            down_price=_HALF,
        )
        result = await adapter.resolve_outcome(opp)
        assert result == "Up"

    @pytest.mark.asyncio
    async def test_resolve_outcome_no_candles(self) -> None:
        """Return None when no candles available."""
        candle_provider = AsyncMock()
        candle_provider.get_candles = AsyncMock(return_value=[])

        adapter = LiveMarketData.__new__(LiveMarketData)
        adapter._candle_provider = candle_provider

        opp = MarketOpportunity(
            condition_id="cond_1",
            title="BTC",
            asset="BTC-USD",
            up_token_id="up",
            down_token_id="down",
            window_start_ts=_BASE_TS,
            window_end_ts=_BASE_TS + 300,
            up_price=_HALF,
            down_price=_HALF,
        )
        result = await adapter.resolve_outcome(opp)
        assert result is None
