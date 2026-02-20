"""Tests for the backtest engine."""

from decimal import Decimal

import pytest

from trading_tools.apps.backtester.engine import BacktestEngine
from trading_tools.core.models import (
    BacktestResult,
    Candle,
    ExecutionConfig,
    Interval,
    RiskConfig,
    Side,
    Signal,
)

EXPECTED_HISTORY_LENGTH_FOR_SELL = 2


def _candle(
    ts: int,
    close: str,
    open_: str = "100",
    high: str | None = None,
    low: str | None = None,
) -> Candle:
    c = Decimal(close)
    o = Decimal(open_)
    h = Decimal(high) if high is not None else max(o, c)
    lo = Decimal(low) if low is not None else min(o, c)
    return Candle(
        symbol="BTC-USD",
        timestamp=ts,
        open=o,
        high=h,
        low=lo,
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


class TestEngineRiskManagement:
    """Tests for stop-loss and take-profit risk management."""

    @pytest.mark.asyncio
    async def test_stop_loss_triggers_on_low(self) -> None:
        """Test that stop-loss exits when candle low breaches the threshold."""
        candles = [
            _candle(1000, "100"),
            _candle(2000, "95", low="93"),  # low breaches 5% SL (95)
        ]
        risk = RiskConfig(stop_loss_pct=Decimal("0.05"))
        engine = BacktestEngine(
            provider=StubProvider(candles),
            strategy=AlwaysBuyStrategy(),
            initial_capital=Decimal(10000),
            risk_config=risk,
        )
        result = await engine.run("BTC-USD", Interval.H1, 0, 3000)
        assert len(result.trades) == 1
        expected_exit = Decimal(95)  # 100 * (1 - 0.05)
        assert result.trades[0].exit_price == expected_exit

    @pytest.mark.asyncio
    async def test_take_profit_triggers_on_high(self) -> None:
        """Test that take-profit exits when candle high breaches the threshold."""
        candles = [
            _candle(1000, "100"),
            _candle(2000, "108", high="112"),  # high breaches 10% TP (110)
        ]
        risk = RiskConfig(take_profit_pct=Decimal("0.10"))
        engine = BacktestEngine(
            provider=StubProvider(candles),
            strategy=AlwaysBuyStrategy(),
            initial_capital=Decimal(10000),
            risk_config=risk,
        )
        result = await engine.run("BTC-USD", Interval.H1, 0, 3000)
        assert len(result.trades) == 1
        expected_exit = Decimal(110)  # 100 * (1 + 0.10)
        assert result.trades[0].exit_price == expected_exit

    @pytest.mark.asyncio
    async def test_both_triggered_stop_loss_wins(self) -> None:
        """Test that stop-loss takes priority when both trigger on same candle."""
        candles = [
            _candle(1000, "100"),
            _candle(2000, "100", high="115", low="90"),  # both trigger
        ]
        risk = RiskConfig(
            stop_loss_pct=Decimal("0.05"),
            take_profit_pct=Decimal("0.10"),
        )
        engine = BacktestEngine(
            provider=StubProvider(candles),
            strategy=AlwaysBuyStrategy(),
            initial_capital=Decimal(10000),
            risk_config=risk,
        )
        result = await engine.run("BTC-USD", Interval.H1, 0, 3000)
        assert len(result.trades) == 1
        expected_exit = Decimal(95)  # stop-loss wins
        assert result.trades[0].exit_price == expected_exit

    @pytest.mark.asyncio
    async def test_no_risk_config_no_exit(self) -> None:
        """Test that without risk config, no automatic exits occur."""
        candles = [
            _candle(1000, "100"),
            _candle(2000, "50", low="40"),  # huge drop but no SL
            _candle(3000, "80"),
        ]
        engine = BacktestEngine(
            provider=StubProvider(candles),
            strategy=AlwaysBuyStrategy(),
            initial_capital=Decimal(10000),
        )
        result = await engine.run("BTC-USD", Interval.H1, 0, 4000)
        assert len(result.trades) == 1
        # force-closed at last candle close, not at the low
        assert result.trades[0].exit_price == Decimal(80)


class TestEngineWithExecutionConfig:
    """Tests for engine with execution config pass-through to portfolio."""

    @pytest.mark.asyncio
    async def test_fees_pass_through(self) -> None:
        """Test that execution config is applied through the engine."""
        candles = [_candle(1000, "100"), _candle(2000, "100")]
        cfg = ExecutionConfig(
            taker_fee_pct=Decimal("0.001"),
            maker_fee_pct=Decimal("0.001"),
        )
        engine = BacktestEngine(
            provider=StubProvider(candles),
            strategy=AlwaysBuyStrategy(),
            initial_capital=Decimal(10000),
            execution_config=cfg,
        )
        result = await engine.run("BTC-USD", Interval.H1, 0, 3000)
        # With fees on a flat price, final capital should be less
        assert result.final_capital < Decimal(10000)

    @pytest.mark.asyncio
    async def test_default_same_results(self) -> None:
        """Test that default execution config produces same results as no config."""
        candles = [_candle(1000, "100"), _candle(2000, "110"), _candle(3000, "120")]
        engine_default = BacktestEngine(
            provider=StubProvider(candles),
            strategy=AlwaysBuyStrategy(),
            initial_capital=Decimal(10000),
        )
        engine_explicit = BacktestEngine(
            provider=StubProvider(candles),
            strategy=AlwaysBuyStrategy(),
            initial_capital=Decimal(10000),
            execution_config=ExecutionConfig(),
        )
        r1 = await engine_default.run("BTC-USD", Interval.H1, 0, 4000)
        r2 = await engine_explicit.run("BTC-USD", Interval.H1, 0, 4000)
        assert r1.final_capital == r2.final_capital

    @pytest.mark.asyncio
    async def test_combined_fees_and_risk(self) -> None:
        """Test that fees and risk management work together correctly."""
        candles = [
            _candle(1000, "100"),
            _candle(2000, "108", high="112"),  # triggers 10% TP
        ]
        cfg = ExecutionConfig(taker_fee_pct=Decimal("0.001"))
        risk = RiskConfig(take_profit_pct=Decimal("0.10"))
        engine = BacktestEngine(
            provider=StubProvider(candles),
            strategy=AlwaysBuyStrategy(),
            initial_capital=Decimal(10000),
            execution_config=cfg,
            risk_config=risk,
        )
        result = await engine.run("BTC-USD", Interval.H1, 0, 3000)
        assert len(result.trades) == 1
        # Entry fee reduces capital, so final is less than 11000
        assert result.final_capital < Decimal(11000)
        assert result.final_capital > Decimal(10000)
