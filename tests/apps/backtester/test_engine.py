"""Tests for the backtest engine."""

from decimal import Decimal

import pytest

from trading_tools.apps.backtester.engine import BacktestEngine
from trading_tools.core.models import (
    BacktestResult,
    Candle,
    Interval,
    Side,
    Signal,
)

EXPECTED_HISTORY_LENGTH_FOR_SELL = 2


def _candle(ts: int, close: str, open_: str = "100") -> Candle:
    c = Decimal(close)
    o = Decimal(open_)
    return Candle(
        symbol="BTC-USD",
        timestamp=ts,
        open=o,
        high=max(o, c),
        low=min(o, c),
        close=c,
        volume=Decimal(10),
        interval=Interval.H1,
    )


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


class AlwaysBuyStrategy:
    """Strategy that emits a buy signal on the first candle only."""

    @property
    def name(self) -> str:
        """Return strategy name."""
        return "always_buy"

    def on_candle(self, candle: Candle, history: list[Candle]) -> Signal | None:
        """Emit buy signal on first candle."""
        if not history:
            return Signal(
                side=Side.BUY,
                symbol=candle.symbol,
                strength=Decimal(1),
                reason="first candle",
            )
        return None


class BuySellStrategy:
    """Buys on first candle, sells on third."""

    @property
    def name(self) -> str:
        """Return strategy name."""
        return "buy_sell"

    def on_candle(self, candle: Candle, history: list[Candle]) -> Signal | None:
        """Emit buy on first candle, sell on third."""
        if len(history) == 0:
            return Signal(
                side=Side.BUY,
                symbol=candle.symbol,
                strength=Decimal(1),
                reason="buy",
            )
        if len(history) == EXPECTED_HISTORY_LENGTH_FOR_SELL:
            return Signal(
                side=Side.SELL,
                symbol=candle.symbol,
                strength=Decimal(1),
                reason="sell",
            )
        return None


class TestBacktestEngine:
    """Tests for BacktestEngine."""

    @pytest.mark.asyncio
    async def test_empty_candles(self) -> None:
        """Test backtest with no candles returns initial capital."""
        engine = BacktestEngine(
            provider=StubProvider([]),
            strategy=AlwaysBuyStrategy(),
            initial_capital=Decimal(10000),
        )
        result = await engine.run("BTC-USD", Interval.H1, 0, 1000)
        assert isinstance(result, BacktestResult)
        assert result.final_capital == Decimal(10000)
        assert result.trades == ()

    @pytest.mark.asyncio
    async def test_force_close_at_end(self) -> None:
        """Test open position is force-closed at last candle."""
        candles = [_candle(1000, "100"), _candle(2000, "110"), _candle(3000, "120")]
        engine = BacktestEngine(
            provider=StubProvider(candles),
            strategy=AlwaysBuyStrategy(),
            initial_capital=Decimal(10000),
        )
        result = await engine.run("BTC-USD", Interval.H1, 0, 4000)
        assert len(result.trades) == 1
        assert result.trades[0].exit_price == Decimal(120)
        assert result.final_capital == Decimal(12000)

    @pytest.mark.asyncio
    async def test_explicit_sell(self) -> None:
        """Test explicit sell signal closes position."""
        candles = [_candle(1000, "100"), _candle(2000, "110"), _candle(3000, "120")]
        engine = BacktestEngine(
            provider=StubProvider(candles),
            strategy=BuySellStrategy(),
            initial_capital=Decimal(10000),
        )
        result = await engine.run("BTC-USD", Interval.H1, 0, 4000)
        assert len(result.trades) == 1
        assert result.trades[0].exit_price == Decimal(120)
        assert result.trades[0].entry_price == Decimal(100)

    @pytest.mark.asyncio
    async def test_result_has_metrics(self) -> None:
        """Test that result includes expected metric keys."""
        candles = [_candle(1000, "100"), _candle(2000, "110"), _candle(3000, "120")]
        engine = BacktestEngine(
            provider=StubProvider(candles),
            strategy=AlwaysBuyStrategy(),
            initial_capital=Decimal(10000),
        )
        result = await engine.run("BTC-USD", Interval.H1, 0, 4000)
        assert "total_return" in result.metrics
        assert "win_rate" in result.metrics
        assert result.metrics["total_return"] == Decimal("0.2")

    @pytest.mark.asyncio
    async def test_strategy_name_in_result(self) -> None:
        """Test strategy name is included in backtest result."""
        engine = BacktestEngine(
            provider=StubProvider([]),
            strategy=AlwaysBuyStrategy(),
            initial_capital=Decimal(10000),
        )
        result = await engine.run("BTC-USD", Interval.H1, 0, 1000)
        assert result.strategy_name == "always_buy"
