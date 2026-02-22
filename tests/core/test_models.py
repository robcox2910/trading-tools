"""Tests for core data models."""

from decimal import Decimal

import pytest

from trading_tools.core.models import (
    BacktestResult,
    Candle,
    ExecutionConfig,
    Interval,
    Position,
    RiskConfig,
    Side,
    Signal,
    Trade,
)


class TestSide:
    """Tests for Side enum."""

    def test_values(self) -> None:
        """Test Side enum values."""
        assert Side.BUY.value == "BUY"
        assert Side.SELL.value == "SELL"


class TestInterval:
    """Tests for Interval enum."""

    def test_all_intervals(self) -> None:
        """Test all interval values are present."""
        expected = {"1m", "5m", "15m", "1h", "4h", "1d", "1w"}
        assert {i.value for i in Interval} == expected


class TestCandle:
    """Tests for Candle model."""

    def test_creation(self) -> None:
        """Test candle creation with all fields."""
        candle = Candle(
            symbol="BTC-USD",
            timestamp=1000000,
            open=Decimal(100),
            high=Decimal(110),
            low=Decimal(90),
            close=Decimal(105),
            volume=Decimal(50),
            interval=Interval.H1,
        )
        assert candle.symbol == "BTC-USD"
        assert candle.close == Decimal(105)
        assert candle.interval == Interval.H1

    def test_frozen(self) -> None:
        """Test candle is immutable."""
        candle = Candle(
            symbol="BTC-USD",
            timestamp=1000000,
            open=Decimal(100),
            high=Decimal(110),
            low=Decimal(90),
            close=Decimal(105),
            volume=Decimal(50),
            interval=Interval.H1,
        )
        with pytest.raises(AttributeError):
            candle.close = Decimal(200)  # type: ignore[misc]


class TestCandleValidation:
    """Tests for Candle OHLCV validation."""

    def test_high_less_than_low_raises(self) -> None:
        """Reject candle where high < low."""
        with pytest.raises(ValueError, match=r"high.*must be >= low"):
            Candle(
                symbol="BTC-USD",
                timestamp=1000,
                open=Decimal(100),
                high=Decimal(90),
                low=Decimal(110),
                close=Decimal(100),
                volume=Decimal(10),
                interval=Interval.H1,
            )

    def test_high_less_than_open_raises(self) -> None:
        """Reject candle where high < open."""
        with pytest.raises(ValueError, match=r"high.*must be >= max"):
            Candle(
                symbol="BTC-USD",
                timestamp=1000,
                open=Decimal(105),
                high=Decimal(104),
                low=Decimal(95),
                close=Decimal(100),
                volume=Decimal(10),
                interval=Interval.H1,
            )

    def test_low_greater_than_close_raises(self) -> None:
        """Reject candle where low > close."""
        with pytest.raises(ValueError, match=r"low.*must be <= min"):
            Candle(
                symbol="BTC-USD",
                timestamp=1000,
                open=Decimal(100),
                high=Decimal(110),
                low=Decimal(96),
                close=Decimal(95),
                volume=Decimal(10),
                interval=Interval.H1,
            )

    def test_negative_volume_raises(self) -> None:
        """Reject candle with negative volume."""
        with pytest.raises(ValueError, match=r"volume.*must be >= 0"):
            Candle(
                symbol="BTC-USD",
                timestamp=1000,
                open=Decimal(100),
                high=Decimal(110),
                low=Decimal(90),
                close=Decimal(105),
                volume=Decimal(-1),
                interval=Interval.H1,
            )

    def test_zero_volume_allowed(self) -> None:
        """Accept candle with zero volume."""
        candle = Candle(
            symbol="BTC-USD",
            timestamp=1000,
            open=Decimal(100),
            high=Decimal(100),
            low=Decimal(100),
            close=Decimal(100),
            volume=Decimal(0),
            interval=Interval.H1,
        )
        assert candle.volume == Decimal(0)


class TestSignal:
    """Tests for Signal model."""

    def test_creation(self) -> None:
        """Test signal creation with valid fields."""
        signal = Signal(
            side=Side.BUY,
            symbol="BTC-USD",
            strength=Decimal("0.8"),
            reason="SMA crossover",
        )
        assert signal.side == Side.BUY
        assert signal.strength == Decimal("0.8")

    def test_strength_bounds(self) -> None:
        """Test signal accepts strength at boundaries."""
        Signal(side=Side.BUY, symbol="X", strength=Decimal(0), reason="ok")
        Signal(side=Side.BUY, symbol="X", strength=Decimal(1), reason="ok")

    def test_strength_too_high(self) -> None:
        """Test signal rejects strength above 1."""
        with pytest.raises(ValueError, match="strength must be between 0 and 1"):
            Signal(side=Side.BUY, symbol="X", strength=Decimal("1.1"), reason="bad")

    def test_strength_negative(self) -> None:
        """Test signal rejects negative strength."""
        with pytest.raises(ValueError, match="strength must be between 0 and 1"):
            Signal(side=Side.BUY, symbol="X", strength=Decimal("-0.1"), reason="bad")


class TestTrade:
    """Tests for Trade model."""

    def _make_trade(self) -> Trade:
        return Trade(
            symbol="BTC-USD",
            side=Side.BUY,
            quantity=Decimal(2),
            entry_price=Decimal(100),
            entry_time=1000,
            exit_price=Decimal(110),
            exit_time=2000,
        )

    def test_pnl(self) -> None:
        """Test profit and loss calculation."""
        trade = self._make_trade()
        assert trade.pnl == Decimal(20)

    def test_pnl_pct(self) -> None:
        """Test profit and loss percentage calculation."""
        trade = self._make_trade()
        assert trade.pnl_pct == Decimal("0.1")

    def test_losing_trade(self) -> None:
        """Test PnL for a losing trade."""
        trade = Trade(
            symbol="BTC-USD",
            side=Side.BUY,
            quantity=Decimal(1),
            entry_price=Decimal(100),
            entry_time=1000,
            exit_price=Decimal(90),
            exit_time=2000,
        )
        assert trade.pnl == Decimal(-10)

    def test_short_trade_pnl(self) -> None:
        """Test PnL for a short trade."""
        trade = Trade(
            symbol="BTC-USD",
            side=Side.SELL,
            quantity=Decimal(2),
            entry_price=Decimal(110),
            entry_time=1000,
            exit_price=Decimal(100),
            exit_time=2000,
        )
        assert trade.pnl == Decimal(20)

    def test_short_trade_pnl_pct(self) -> None:
        """Test PnL percentage for a short trade."""
        trade = Trade(
            symbol="BTC-USD",
            side=Side.SELL,
            quantity=Decimal(2),
            entry_price=Decimal(110),
            entry_time=1000,
            exit_price=Decimal(100),
            exit_time=2000,
        )
        assert trade.pnl_pct == Decimal(10) / Decimal(110)

    def test_pnl_pct_zero_cost_basis(self) -> None:
        """Return ZERO when cost basis is zero to avoid ZeroDivisionError."""
        trade = Trade(
            symbol="BTC-USD",
            side=Side.BUY,
            quantity=Decimal(0),
            entry_price=Decimal(0),
            entry_time=1000,
            exit_price=Decimal(100),
            exit_time=2000,
        )
        assert trade.pnl_pct == Decimal(0)


class TestTradeWithFees:
    """Tests for Trade PnL calculations when fees are present."""

    def test_pnl_with_fees(self) -> None:
        """Test that fees are subtracted from raw PnL."""
        trade = Trade(
            symbol="BTC-USD",
            side=Side.BUY,
            quantity=Decimal(2),
            entry_price=Decimal(100),
            entry_time=1000,
            exit_price=Decimal(110),
            exit_time=2000,
            entry_fee=Decimal(5),
            exit_fee=Decimal(3),
        )
        # raw pnl = (110-100)*2 = 20, net = 20 - 5 - 3 = 12
        expected_pnl = Decimal(12)
        assert trade.pnl == expected_pnl

    def test_pnl_pct_with_fees(self) -> None:
        """Test that pnl_pct uses cost basis (entry_value + entry_fee)."""
        trade = Trade(
            symbol="BTC-USD",
            side=Side.BUY,
            quantity=Decimal(2),
            entry_price=Decimal(100),
            entry_time=1000,
            exit_price=Decimal(110),
            exit_time=2000,
            entry_fee=Decimal(5),
            exit_fee=Decimal(3),
        )
        # cost_basis = 100*2 + 5 = 205, net_pnl = 12
        expected_pct = Decimal(12) / Decimal(205)
        assert trade.pnl_pct == expected_pct

    def test_zero_fees_backward_compat(self) -> None:
        """Test that default zero fees produce the same results as before."""
        trade = Trade(
            symbol="BTC-USD",
            side=Side.BUY,
            quantity=Decimal(2),
            entry_price=Decimal(100),
            entry_time=1000,
            exit_price=Decimal(110),
            exit_time=2000,
        )
        expected_pnl = Decimal(20)
        expected_pct = Decimal("0.1")
        assert trade.pnl == expected_pnl
        assert trade.pnl_pct == expected_pct


class TestExecutionConfig:
    """Tests for ExecutionConfig defaults and construction."""

    def test_defaults(self) -> None:
        """Test all defaults are zero/one for backward compatibility."""
        cfg = ExecutionConfig()
        assert cfg.maker_fee_pct == Decimal(0)
        assert cfg.taker_fee_pct == Decimal(0)
        assert cfg.slippage_pct == Decimal(0)
        assert cfg.position_size_pct == Decimal(1)

    def test_custom_values(self) -> None:
        """Test constructing with custom fee and sizing values."""
        cfg = ExecutionConfig(
            maker_fee_pct=Decimal("0.001"),
            taker_fee_pct=Decimal("0.002"),
            slippage_pct=Decimal("0.0005"),
            position_size_pct=Decimal("0.5"),
        )
        assert cfg.maker_fee_pct == Decimal("0.001")
        assert cfg.position_size_pct == Decimal("0.5")

    def test_frozen(self) -> None:
        """Test ExecutionConfig is immutable."""
        cfg = ExecutionConfig()
        with pytest.raises(AttributeError):
            cfg.maker_fee_pct = Decimal("0.01")  # type: ignore[misc]


class TestRiskConfig:
    """Tests for RiskConfig defaults and construction."""

    def test_defaults(self) -> None:
        """Test defaults are None (no risk exits)."""
        cfg = RiskConfig()
        assert cfg.stop_loss_pct is None
        assert cfg.take_profit_pct is None

    def test_custom_values(self) -> None:
        """Test constructing with stop-loss and take-profit thresholds."""
        cfg = RiskConfig(
            stop_loss_pct=Decimal("0.05"),
            take_profit_pct=Decimal("0.10"),
        )
        assert cfg.stop_loss_pct == Decimal("0.05")
        assert cfg.take_profit_pct == Decimal("0.10")


class TestPosition:
    """Tests for Position model."""

    def test_close_returns_trade(self) -> None:
        """Test closing a position returns a Trade."""
        pos = Position(
            symbol="ETH-USD",
            side=Side.BUY,
            quantity=Decimal(5),
            entry_price=Decimal(200),
            entry_time=1000,
        )
        trade = pos.close(exit_price=Decimal(250), exit_time=2000)
        assert isinstance(trade, Trade)
        assert trade.pnl == Decimal(250)
        assert trade.entry_price == Decimal(200)
        assert trade.exit_price == Decimal(250)

    def test_mutable(self) -> None:
        """Test position fields are mutable."""
        pos = Position(
            symbol="ETH-USD",
            side=Side.BUY,
            quantity=Decimal(5),
            entry_price=Decimal(200),
            entry_time=1000,
        )
        pos.quantity = Decimal(10)
        assert pos.quantity == Decimal(10)


class TestBacktestResult:
    """Tests for BacktestResult model."""

    def test_creation(self) -> None:
        """Test backtest result creation with all fields."""
        result = BacktestResult(
            strategy_name="SMA",
            symbol="BTC-USD",
            interval=Interval.H1,
            initial_capital=Decimal(10000),
            final_capital=Decimal(11000),
            trades=(),
            metrics={"sharpe": Decimal("1.5")},
        )
        assert result.strategy_name == "SMA"
        assert result.final_capital == Decimal(11000)
        assert result.metrics["sharpe"] == Decimal("1.5")

    def test_default_metrics(self) -> None:
        """Test backtest result defaults to empty metrics."""
        result = BacktestResult(
            strategy_name="SMA",
            symbol="BTC-USD",
            interval=Interval.H1,
            initial_capital=Decimal(10000),
            final_capital=Decimal(10000),
            trades=(),
        )
        assert result.metrics == {}
