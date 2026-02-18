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
        volume=Decimal("10"),
        interval=Interval.H1,
    )


class StubProvider:
    def __init__(self, candles: list[Candle]) -> None:
        self._candles = candles

    async def get_candles(
        self, symbol: str, interval: Interval, start_ts: int, end_ts: int
    ) -> list[Candle]:
        return self._candles


class AlwaysBuyStrategy:
    @property
    def name(self) -> str:
        return "always_buy"

    def on_candle(self, candle: Candle, history: list[Candle]) -> Signal | None:
        if not history:
            return Signal(
                side=Side.BUY,
                symbol=candle.symbol,
                strength=Decimal("1"),
                reason="first candle",
            )
        return None


class BuySellStrategy:
    """Buys on first candle, sells on third."""

    @property
    def name(self) -> str:
        return "buy_sell"

    def on_candle(self, candle: Candle, history: list[Candle]) -> Signal | None:
        if len(history) == 0:
            return Signal(
                side=Side.BUY,
                symbol=candle.symbol,
                strength=Decimal("1"),
                reason="buy",
            )
        if len(history) == 2:
            return Signal(
                side=Side.SELL,
                symbol=candle.symbol,
                strength=Decimal("1"),
                reason="sell",
            )
        return None


class TestBacktestEngine:
    @pytest.mark.asyncio
    async def test_empty_candles(self) -> None:
        engine = BacktestEngine(
            provider=StubProvider([]),
            strategy=AlwaysBuyStrategy(),
            initial_capital=Decimal("10000"),
        )
        result = await engine.run("BTC-USD", Interval.H1, 0, 1000)
        assert isinstance(result, BacktestResult)
        assert result.final_capital == Decimal("10000")
        assert result.trades == ()

    @pytest.mark.asyncio
    async def test_force_close_at_end(self) -> None:
        candles = [_candle(1000, "100"), _candle(2000, "110"), _candle(3000, "120")]
        engine = BacktestEngine(
            provider=StubProvider(candles),
            strategy=AlwaysBuyStrategy(),
            initial_capital=Decimal("10000"),
        )
        result = await engine.run("BTC-USD", Interval.H1, 0, 4000)
        assert len(result.trades) == 1
        assert result.trades[0].exit_price == Decimal("120")
        assert result.final_capital == Decimal("12000")

    @pytest.mark.asyncio
    async def test_explicit_sell(self) -> None:
        candles = [_candle(1000, "100"), _candle(2000, "110"), _candle(3000, "120")]
        engine = BacktestEngine(
            provider=StubProvider(candles),
            strategy=BuySellStrategy(),
            initial_capital=Decimal("10000"),
        )
        result = await engine.run("BTC-USD", Interval.H1, 0, 4000)
        assert len(result.trades) == 1
        assert result.trades[0].exit_price == Decimal("120")
        assert result.trades[0].entry_price == Decimal("100")

    @pytest.mark.asyncio
    async def test_result_has_metrics(self) -> None:
        candles = [_candle(1000, "100"), _candle(2000, "110"), _candle(3000, "120")]
        engine = BacktestEngine(
            provider=StubProvider(candles),
            strategy=AlwaysBuyStrategy(),
            initial_capital=Decimal("10000"),
        )
        result = await engine.run("BTC-USD", Interval.H1, 0, 4000)
        assert "total_return" in result.metrics
        assert "win_rate" in result.metrics
        assert result.metrics["total_return"] == Decimal("0.2")

    @pytest.mark.asyncio
    async def test_strategy_name_in_result(self) -> None:
        engine = BacktestEngine(
            provider=StubProvider([]),
            strategy=AlwaysBuyStrategy(),
            initial_capital=Decimal("10000"),
        )
        result = await engine.run("BTC-USD", Interval.H1, 0, 1000)
        assert result.strategy_name == "always_buy"
