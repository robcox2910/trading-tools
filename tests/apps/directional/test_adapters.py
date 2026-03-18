"""Tests for directional trading adapters."""

from decimal import Decimal

import pytest

from trading_tools.apps.directional.adapters import (
    BacktestExecution,
    PaperExecution,
    ReplayMarketData,
)
from trading_tools.apps.directional.models import MarketOpportunity
from trading_tools.core.models import ZERO, Candle, Interval

_CAPITAL = Decimal(1000)
_PRICE = Decimal("0.55")
_QTY = Decimal(20)
_SLIPPAGE = Decimal("0.005")
_WINDOW_START = 1_710_000_000
_WINDOW_END = 1_710_000_300


def _make_opportunity(condition_id: str = "cond_1") -> MarketOpportunity:
    """Create a test MarketOpportunity."""
    return MarketOpportunity(
        condition_id=condition_id,
        title="BTC Up or Down?",
        asset="BTC-USD",
        up_token_id="up_tok",
        down_token_id="down_tok",
        window_start_ts=_WINDOW_START,
        window_end_ts=_WINDOW_END,
        up_price=Decimal("0.55"),
        down_price=Decimal("0.45"),
    )


def _make_candle(ts: int, close: Decimal = Decimal(100)) -> Candle:
    """Create a test candle."""
    return Candle(
        symbol="BTC-USD",
        timestamp=ts,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=Decimal(1000),
        interval=Interval.M1,
    )


class TestPaperExecution:
    """Test virtual capital execution with slippage."""

    @pytest.mark.asyncio
    async def test_fill_with_slippage(self) -> None:
        """Fill applies slippage to the price."""
        adapter = PaperExecution(capital=_CAPITAL, slippage_pct=_SLIPPAGE)
        result = await adapter.execute_fill("tok", "BUY", _PRICE, _QTY)
        assert result is not None
        expected_price = _PRICE * (Decimal(1) + _SLIPPAGE)
        assert result.price == expected_price
        assert result.quantity == _QTY

    @pytest.mark.asyncio
    async def test_fill_deducts_capital(self) -> None:
        """Successful fill reduces available capital."""
        adapter = PaperExecution(capital=_CAPITAL, slippage_pct=ZERO)
        await adapter.execute_fill("tok", "BUY", _PRICE, _QTY)
        expected_remaining = _CAPITAL - _PRICE * _QTY
        assert adapter.get_capital() == expected_remaining

    @pytest.mark.asyncio
    async def test_insufficient_capital_returns_none(self) -> None:
        """Fill returns None when capital is insufficient."""
        adapter = PaperExecution(capital=Decimal(1), slippage_pct=ZERO)
        result = await adapter.execute_fill("tok", "BUY", _PRICE, _QTY)
        assert result is None

    def test_total_capital(self) -> None:
        """Total capital returns the initial amount."""
        adapter = PaperExecution(capital=_CAPITAL)
        assert adapter.total_capital() == _CAPITAL

    def test_add_capital(self) -> None:
        """Add capital adjusts both available and total."""
        adapter = PaperExecution(capital=_CAPITAL)
        adapter.add_capital(Decimal(50))
        assert adapter.get_capital() == _CAPITAL + Decimal(50)
        assert adapter.total_capital() == _CAPITAL + Decimal(50)


class TestBacktestExecution:
    """Test deterministic backtest execution."""

    @pytest.mark.asyncio
    async def test_fill_at_exact_price(self) -> None:
        """Backtest fills at the exact requested price."""
        adapter = BacktestExecution(capital=_CAPITAL)
        result = await adapter.execute_fill("tok", "BUY", _PRICE, _QTY)
        assert result is not None
        assert result.price == _PRICE

    @pytest.mark.asyncio
    async def test_insufficient_capital_returns_none(self) -> None:
        """Returns None when capital is insufficient."""
        adapter = BacktestExecution(capital=Decimal(1))
        result = await adapter.execute_fill("tok", "BUY", _PRICE, _QTY)
        assert result is None

    def test_add_capital(self) -> None:
        """Add capital adjusts available and total."""
        adapter = BacktestExecution(capital=_CAPITAL)
        adapter.add_capital(Decimal(-10))
        assert adapter.get_capital() == _CAPITAL - Decimal(10)


class TestReplayMarketData:
    """Test pre-loaded market data replay."""

    @pytest.mark.asyncio
    async def test_get_active_markets_excludes_open(self) -> None:
        """Already-open markets are excluded."""
        adapter = ReplayMarketData()
        m1 = _make_opportunity("cond_1")
        m2 = _make_opportunity("cond_2")
        adapter.set_markets([m1, m2])
        result = await adapter.get_active_markets({"cond_1"})
        assert len(result) == 1
        assert result[0].condition_id == "cond_2"

    @pytest.mark.asyncio
    async def test_get_binance_candles_filters_by_time(self) -> None:
        """Candles are filtered by time range."""
        adapter = ReplayMarketData()
        c1 = _make_candle(100)
        c2 = _make_candle(200)
        c3 = _make_candle(300)
        adapter.set_candles("BTC-USD", [c1, c2, c3])
        result = await adapter.get_binance_candles("BTC-USD", 150, 250)
        assert len(result) == 1
        assert result[0].timestamp == 200  # noqa: PLR2004

    @pytest.mark.asyncio
    async def test_resolve_outcome(self) -> None:
        """Return registered outcome or None."""
        adapter = ReplayMarketData()
        opp = _make_opportunity("cond_1")
        adapter.set_outcome("cond_1", "Up")
        assert await adapter.resolve_outcome(opp) == "Up"

    @pytest.mark.asyncio
    async def test_resolve_outcome_unknown(self) -> None:
        """Return None for unregistered markets."""
        adapter = ReplayMarketData()
        opp = _make_opportunity("cond_unknown")
        assert await adapter.resolve_outcome(opp) is None
