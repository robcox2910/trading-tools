"""Tests for core data models."""

from decimal import Decimal

import pytest

from trading_tools.core.models import (
    BacktestResult,
    Candle,
    Interval,
    Position,
    Side,
    Signal,
    Trade,
)


class TestSide:
    def test_values(self) -> None:
        assert Side.BUY.value == "BUY"
        assert Side.SELL.value == "SELL"


class TestInterval:
    def test_all_intervals(self) -> None:
        expected = {"1m", "5m", "15m", "1h", "4h", "1d", "1w"}
        assert {i.value for i in Interval} == expected


class TestCandle:
    def test_creation(self) -> None:
        candle = Candle(
            symbol="BTC-USD",
            timestamp=1000000,
            open=Decimal("100"),
            high=Decimal("110"),
            low=Decimal("90"),
            close=Decimal("105"),
            volume=Decimal("50"),
            interval=Interval.H1,
        )
        assert candle.symbol == "BTC-USD"
        assert candle.close == Decimal("105")
        assert candle.interval == Interval.H1

    def test_frozen(self) -> None:
        candle = Candle(
            symbol="BTC-USD",
            timestamp=1000000,
            open=Decimal("100"),
            high=Decimal("110"),
            low=Decimal("90"),
            close=Decimal("105"),
            volume=Decimal("50"),
            interval=Interval.H1,
        )
        with pytest.raises(AttributeError):
            candle.close = Decimal("200")  # type: ignore[misc]


class TestSignal:
    def test_creation(self) -> None:
        signal = Signal(
            side=Side.BUY,
            symbol="BTC-USD",
            strength=Decimal("0.8"),
            reason="SMA crossover",
        )
        assert signal.side == Side.BUY
        assert signal.strength == Decimal("0.8")

    def test_strength_bounds(self) -> None:
        Signal(side=Side.BUY, symbol="X", strength=Decimal("0"), reason="ok")
        Signal(side=Side.BUY, symbol="X", strength=Decimal("1"), reason="ok")

    def test_strength_too_high(self) -> None:
        with pytest.raises(ValueError, match="strength must be between 0 and 1"):
            Signal(side=Side.BUY, symbol="X", strength=Decimal("1.1"), reason="bad")

    def test_strength_negative(self) -> None:
        with pytest.raises(ValueError, match="strength must be between 0 and 1"):
            Signal(side=Side.BUY, symbol="X", strength=Decimal("-0.1"), reason="bad")


class TestTrade:
    def _make_trade(self) -> Trade:
        return Trade(
            symbol="BTC-USD",
            side=Side.BUY,
            quantity=Decimal("2"),
            entry_price=Decimal("100"),
            entry_time=1000,
            exit_price=Decimal("110"),
            exit_time=2000,
        )

    def test_pnl(self) -> None:
        trade = self._make_trade()
        assert trade.pnl == Decimal("20")

    def test_pnl_pct(self) -> None:
        trade = self._make_trade()
        assert trade.pnl_pct == Decimal("0.1")

    def test_losing_trade(self) -> None:
        trade = Trade(
            symbol="BTC-USD",
            side=Side.BUY,
            quantity=Decimal("1"),
            entry_price=Decimal("100"),
            entry_time=1000,
            exit_price=Decimal("90"),
            exit_time=2000,
        )
        assert trade.pnl == Decimal("-10")


class TestPosition:
    def test_close_returns_trade(self) -> None:
        pos = Position(
            symbol="ETH-USD",
            side=Side.BUY,
            quantity=Decimal("5"),
            entry_price=Decimal("200"),
            entry_time=1000,
        )
        trade = pos.close(exit_price=Decimal("250"), exit_time=2000)
        assert isinstance(trade, Trade)
        assert trade.pnl == Decimal("250")
        assert trade.entry_price == Decimal("200")
        assert trade.exit_price == Decimal("250")

    def test_mutable(self) -> None:
        pos = Position(
            symbol="ETH-USD",
            side=Side.BUY,
            quantity=Decimal("5"),
            entry_price=Decimal("200"),
            entry_time=1000,
        )
        pos.quantity = Decimal("10")
        assert pos.quantity == Decimal("10")


class TestBacktestResult:
    def test_creation(self) -> None:
        result = BacktestResult(
            strategy_name="SMA",
            symbol="BTC-USD",
            interval=Interval.H1,
            initial_capital=Decimal("10000"),
            final_capital=Decimal("11000"),
            trades=(),
            metrics={"sharpe": Decimal("1.5")},
        )
        assert result.strategy_name == "SMA"
        assert result.final_capital == Decimal("11000")
        assert result.metrics["sharpe"] == Decimal("1.5")

    def test_default_metrics(self) -> None:
        result = BacktestResult(
            strategy_name="SMA",
            symbol="BTC-USD",
            interval=Interval.H1,
            initial_capital=Decimal("10000"),
            final_capital=Decimal("10000"),
            trades=(),
        )
        assert result.metrics == {}
