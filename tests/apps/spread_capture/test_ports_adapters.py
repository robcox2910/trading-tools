"""Tests for spread capture port protocols and adapter implementations."""

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from trading_tools.apps.spread_capture.adapters import (
    BacktestExecution,
    PaperExecution,
    ReplayMarketData,
)
from trading_tools.apps.spread_capture.models import SpreadOpportunity
from trading_tools.apps.spread_capture.ports import ExecutionPort, FillResult, MarketDataPort
from trading_tools.core.models import ZERO


class TestFillResult:
    """Test the FillResult dataclass."""

    def test_fill_result_creation(self) -> None:
        """FillResult stores price, quantity, and optional order_id."""
        result = FillResult(price=Decimal("0.50"), quantity=Decimal(10))
        assert result.price == Decimal("0.50")
        assert result.quantity == Decimal(10)
        assert result.order_id is None

    def test_fill_result_with_order_id(self) -> None:
        """FillResult can include a CLOB order ID."""
        result = FillResult(price=Decimal("0.50"), quantity=Decimal(10), order_id="order_123")
        assert result.order_id == "order_123"


class TestProtocolConformance:
    """Verify adapters satisfy the protocol interfaces."""

    def test_paper_execution_is_execution_port(self) -> None:
        """PaperExecution satisfies the ExecutionPort protocol."""
        adapter = PaperExecution(base_capital=Decimal(100), slippage_pct=ZERO)
        assert isinstance(adapter, ExecutionPort)

    def test_backtest_execution_is_execution_port(self) -> None:
        """BacktestExecution satisfies the ExecutionPort protocol."""
        adapter = BacktestExecution(capital=Decimal(100))
        assert isinstance(adapter, ExecutionPort)

    def test_replay_market_data_is_market_data_port(self) -> None:
        """ReplayMarketData satisfies the MarketDataPort protocol."""
        adapter = ReplayMarketData(
            opportunities=[],
            up_books={},
            down_books={},
            candles={},
        )
        assert isinstance(adapter, MarketDataPort)


@pytest.mark.asyncio
class TestPaperExecution:
    """Test the PaperExecution adapter."""

    async def test_fill_with_zero_slippage(self) -> None:
        """Paper fill with zero slippage returns exact price."""
        adapter = PaperExecution(base_capital=Decimal(100), slippage_pct=ZERO)
        result = await adapter.execute_fill("tok", "BUY", Decimal("0.50"), Decimal(10))
        assert result is not None
        assert result.price == Decimal("0.50")
        assert result.quantity == Decimal(10)

    async def test_fill_with_slippage(self) -> None:
        """Paper fill applies slippage to the price."""
        adapter = PaperExecution(base_capital=Decimal(100), slippage_pct=Decimal("0.01"))
        result = await adapter.execute_fill("tok", "BUY", Decimal("0.50"), Decimal(10))
        assert result is not None
        assert result.price == Decimal("0.505")

    async def test_get_capital(self) -> None:
        """Available capital subtracts committed capital."""
        adapter = PaperExecution(
            base_capital=Decimal(100),
            slippage_pct=ZERO,
            committed_capital_fn=lambda: Decimal(30),
        )
        assert adapter.get_capital() == Decimal(70)

    async def test_total_capital_with_compound(self) -> None:
        """Total capital adds realised P&L when compounding."""
        adapter = PaperExecution(
            base_capital=Decimal(100),
            slippage_pct=ZERO,
            compound_profits=True,
            realised_pnl_fn=lambda: Decimal(15),
        )
        assert adapter.total_capital() == Decimal(115)


@pytest.mark.asyncio
class TestBacktestExecution:
    """Test the BacktestExecution adapter."""

    async def test_fill_succeeds(self) -> None:
        """Backtest fill always succeeds."""
        adapter = BacktestExecution(capital=Decimal(1000))
        result = await adapter.execute_fill("tok", "BUY", Decimal("0.45"), Decimal(20))
        assert result is not None
        assert result.price == Decimal("0.45")
        assert result.quantity == Decimal(20)

    async def test_get_capital_subtracts_committed(self) -> None:
        """Available capital subtracts committed."""
        adapter = BacktestExecution(
            capital=Decimal(1000),
            committed_capital_fn=lambda: Decimal(200),
        )
        assert adapter.get_capital() == Decimal(800)


@pytest.mark.asyncio
class TestReplayMarketData:
    """Test the ReplayMarketData adapter."""

    async def test_opportunities_served_once(self) -> None:
        """Opportunities are returned on first call, empty on subsequent."""
        opp = SpreadOpportunity(
            condition_id="cond_1",
            title="Test",
            asset="BTC-USD",
            up_token_id="up",
            down_token_id="down",
            up_price=Decimal("0.50"),
            down_price=Decimal("0.50"),
            combined=Decimal("1.00"),
            margin=ZERO,
            window_start_ts=1000,
            window_end_ts=2000,
        )
        adapter = ReplayMarketData(
            opportunities=[opp],
            up_books={},
            down_books={},
            candles={},
        )

        first = await adapter.get_opportunities(set())
        assert len(first) == 1
        second = await adapter.get_opportunities(set())
        assert len(second) == 0

    async def test_resolve_outcome(self) -> None:
        """Resolve returns the pre-set outcome."""
        adapter = ReplayMarketData(
            opportunities=[],
            up_books={},
            down_books={},
            candles={},
            outcome="Up",
        )
        opp = SpreadOpportunity(
            condition_id="c",
            title="T",
            asset="BTC-USD",
            up_token_id="u",
            down_token_id="d",
            up_price=ZERO,
            down_price=ZERO,
            combined=ZERO,
            margin=ZERO,
            window_start_ts=0,
            window_end_ts=0,
        )
        assert await adapter.resolve_outcome(opp) == "Up"

    async def test_empty_books_returns_empty_order_book(self) -> None:
        """Empty books dict returns order books with no levels."""
        adapter = ReplayMarketData(
            opportunities=[],
            up_books={},
            down_books={},
            candles={},
        )
        adapter.clock = 1500
        up_book, down_book = await adapter.get_order_books("up", "down")
        assert len(up_book.asks) == 0
        assert len(down_book.asks) == 0

    async def test_candles_filtered_by_range(self) -> None:
        """Get candles returns only those within the time range."""
        c1 = MagicMock(timestamp=100)
        c2 = MagicMock(timestamp=200)
        c3 = MagicMock(timestamp=300)

        adapter = ReplayMarketData(
            opportunities=[],
            up_books={},
            down_books={},
            candles={"BTC-USD": [c1, c2, c3]},
        )
        result = await adapter.get_binance_candles("BTC-USD", 150, 250)
        assert result == [c2]

    async def test_whale_signal_returns_none(self) -> None:
        """Replay adapter always returns None for whale signal."""
        adapter = ReplayMarketData(
            opportunities=[],
            up_books={},
            down_books={},
            candles={},
        )
        result = await adapter.get_whale_signal("cond_1", 1000)
        assert result is None
