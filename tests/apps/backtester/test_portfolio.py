"""Tests for portfolio state tracking."""

from decimal import Decimal

from trading_tools.apps.backtester.portfolio import Portfolio
from trading_tools.core.models import (
    Candle,
    ExecutionConfig,
    Interval,
    RiskConfig,
    Side,
    Signal,
    Trade,
)

EXPECTED_ROUND_TRIP_COUNT = 2


def _buy_signal(symbol: str = "BTC-USD") -> Signal:
    return Signal(side=Side.BUY, symbol=symbol, strength=Decimal(1), reason="test buy")


def _sell_signal(symbol: str = "BTC-USD") -> Signal:
    return Signal(side=Side.SELL, symbol=symbol, strength=Decimal(1), reason="test sell")


class TestPortfolio:
    """Tests for Portfolio state management."""

    def test_initial_state(self) -> None:
        """Test portfolio starts with given capital and no position."""
        p = Portfolio(Decimal(10000))
        assert p.capital == Decimal(10000)
        assert p.position is None
        assert p.trades == []

    def test_buy_opens_position(self) -> None:
        """Test buy signal opens a new position."""
        p = Portfolio(Decimal(10000))
        result = p.process_signal(_buy_signal(), Decimal(100), 1000)
        assert result is None
        assert p.position is not None
        assert p.position.entry_price == Decimal(100)
        assert p.position.quantity == Decimal(100)
        assert p.capital == Decimal(0)

    def test_sell_closes_position(self) -> None:
        """Test sell signal closes an open position."""
        p = Portfolio(Decimal(10000))
        p.process_signal(_buy_signal(), Decimal(100), 1000)
        trade = p.process_signal(_sell_signal(), Decimal(110), 2000)
        assert isinstance(trade, Trade)
        assert trade.pnl == Decimal(1000)
        assert p.position is None
        assert p.capital == Decimal(11000)

    def test_sell_without_position_ignored(self) -> None:
        """Test sell signal without open position is ignored."""
        p = Portfolio(Decimal(10000))
        result = p.process_signal(_sell_signal(), Decimal(100), 1000)
        assert result is None
        assert p.capital == Decimal(10000)

    def test_buy_with_existing_position_ignored(self) -> None:
        """Test buy signal with existing position is ignored."""
        p = Portfolio(Decimal(10000))
        p.process_signal(_buy_signal(), Decimal(100), 1000)
        result = p.process_signal(_buy_signal(), Decimal(110), 2000)
        assert result is None
        assert p.position is not None
        assert p.position.entry_price == Decimal(100)

    def test_force_close(self) -> None:
        """Test force-closing an open position."""
        p = Portfolio(Decimal(10000))
        p.process_signal(_buy_signal(), Decimal(100), 1000)
        trade = p.force_close(Decimal(120), 3000)
        assert isinstance(trade, Trade)
        assert trade.exit_price == Decimal(120)
        assert p.position is None
        assert p.capital == Decimal(12000)

    def test_force_close_no_position(self) -> None:
        """Test force-close with no open position returns None."""
        p = Portfolio(Decimal(10000))
        result = p.force_close(Decimal(100), 1000)
        assert result is None

    def test_multiple_round_trips(self) -> None:
        """Test multiple buy-sell round trips accumulate correctly."""
        p = Portfolio(Decimal(10000))
        p.process_signal(_buy_signal(), Decimal(100), 1000)
        p.process_signal(_sell_signal(), Decimal(110), 2000)
        p.process_signal(_buy_signal(), Decimal(110), 3000)
        p.process_signal(_sell_signal(), Decimal(121), 4000)
        assert len(p.trades) == EXPECTED_ROUND_TRIP_COUNT
        assert p.capital == Decimal(12100)

    def test_losing_trade(self) -> None:
        """Test losing trade reduces capital."""
        p = Portfolio(Decimal(10000))
        p.process_signal(_buy_signal(), Decimal(100), 1000)
        trade = p.process_signal(_sell_signal(), Decimal(90), 2000)
        assert trade is not None
        assert trade.pnl == Decimal(-1000)
        assert p.capital == Decimal(9000)

    def test_trades_list_is_copy(self) -> None:
        """Test that trades property returns a copy."""
        p = Portfolio(Decimal(10000))
        trades = p.trades
        trades.append(None)  # type: ignore[arg-type]
        assert p.trades == []


class TestPortfolioWithFees:
    """Tests for Portfolio with execution fees and slippage."""

    def test_slippage_worsens_entry_price(self) -> None:
        """Test that slippage increases the effective entry price."""
        cfg = ExecutionConfig(slippage_pct=Decimal("0.01"))
        p = Portfolio(Decimal(10000), execution_config=cfg)
        p.process_signal(_buy_signal(), Decimal(100), 1000)
        assert p.position is not None
        expected_price = Decimal(101)  # 100 * 1.01
        assert p.position.entry_price == expected_price

    def test_slippage_worsens_exit_price(self) -> None:
        """Test that slippage decreases the effective exit price."""
        cfg = ExecutionConfig(slippage_pct=Decimal("0.01"))
        p = Portfolio(Decimal(10000), execution_config=cfg)
        p.process_signal(_buy_signal(), Decimal(100), 1000)
        trade = p.process_signal(_sell_signal(), Decimal(110), 2000)
        assert trade is not None
        expected_exit = Decimal("108.9")  # 110 * 0.99
        assert trade.exit_price == expected_exit

    def test_fees_deducted(self) -> None:
        """Test that entry and exit fees are recorded on the trade."""
        cfg = ExecutionConfig(
            taker_fee_pct=Decimal("0.001"),
            maker_fee_pct=Decimal("0.001"),
        )
        p = Portfolio(Decimal(10000), execution_config=cfg)
        p.process_signal(_buy_signal(), Decimal(100), 1000)
        trade = p.process_signal(_sell_signal(), Decimal(100), 2000)
        assert trade is not None
        assert trade.entry_fee == Decimal(10)  # 10000 * 0.001
        assert trade.exit_fee > Decimal(0)

    def test_round_trip_with_fees_reduces_capital(self) -> None:
        """Test that a flat-price round trip loses money due to fees."""
        cfg = ExecutionConfig(
            taker_fee_pct=Decimal("0.001"),
            maker_fee_pct=Decimal("0.001"),
        )
        p = Portfolio(Decimal(10000), execution_config=cfg)
        p.process_signal(_buy_signal(), Decimal(100), 1000)
        p.process_signal(_sell_signal(), Decimal(100), 2000)
        assert p.capital < Decimal(10000)


class TestPortfolioPositionSizing:
    """Tests for Portfolio position sizing."""

    def test_half_position_uses_half_capital(self) -> None:
        """Test that position_size_pct=0.5 deploys only half the capital."""
        cfg = ExecutionConfig(position_size_pct=Decimal("0.5"))
        p = Portfolio(Decimal(10000), execution_config=cfg)
        p.process_signal(_buy_signal(), Decimal(100), 1000)
        assert p.position is not None
        expected_qty = Decimal(50)  # 5000 / 100
        assert p.position.quantity == expected_qty

    def test_remaining_capital_preserved(self) -> None:
        """Test that unused capital is preserved when position_size_pct < 1."""
        cfg = ExecutionConfig(position_size_pct=Decimal("0.5"))
        p = Portfolio(Decimal(10000), execution_config=cfg)
        p.process_signal(_buy_signal(), Decimal(100), 1000)
        expected_remaining = Decimal(5000)
        assert p.capital == expected_remaining

    def test_default_full_deployment(self) -> None:
        """Test that default position_size_pct=1 deploys all capital."""
        p = Portfolio(Decimal(10000))
        p.process_signal(_buy_signal(), Decimal(100), 1000)
        assert p.capital == Decimal(0)
        assert p.position is not None
        expected_qty = Decimal(100)
        assert p.position.quantity == expected_qty


class TestCircuitBreaker:
    """Tests for the drawdown circuit breaker in Portfolio."""

    def test_halts_at_drawdown_threshold(self) -> None:
        """Halt trading when equity drawdown exceeds circuit_breaker_pct."""
        risk = RiskConfig(circuit_breaker_pct=Decimal("0.10"))
        p = Portfolio(Decimal(10000), risk_config=risk)
        # Open and close with a loss that triggers 15% drawdown
        p.process_signal(_buy_signal(), Decimal(100), 1000)
        p.process_signal(_sell_signal(), Decimal(85), 2000)
        # equity is now 8500, peak was 10000 -> 15% drawdown > 10%
        p.update_equity(Decimal(85))
        assert p.halted is True

    def test_buy_signals_skipped_while_halted(self) -> None:
        """Skip BUY signals when circuit breaker is active."""
        risk = RiskConfig(circuit_breaker_pct=Decimal("0.10"))
        p = Portfolio(Decimal(10000), risk_config=risk)
        p.process_signal(_buy_signal(), Decimal(100), 1000)
        p.process_signal(_sell_signal(), Decimal(85), 2000)
        p.update_equity(Decimal(85))
        assert p.halted is True
        # Try to buy — should be skipped
        result = p.process_signal(_buy_signal(), Decimal(85), 3000)
        assert result is None
        assert p.position is None

    def test_sell_works_while_halted(self) -> None:
        """Allow SELL signals to close positions opened before halt."""
        risk = RiskConfig(circuit_breaker_pct=Decimal("0.05"))
        p = Portfolio(Decimal(10000), risk_config=risk)
        p.process_signal(_buy_signal(), Decimal(100), 1000)
        # Price drops enough to trigger halt while position is still open
        p.update_equity(Decimal(90))
        assert p.halted is True
        # SELL should still work to close the position
        trade = p.process_signal(_sell_signal(), Decimal(90), 2000)
        assert isinstance(trade, Trade)

    def test_force_close_works_while_halted(self) -> None:
        """Allow force_close while circuit breaker is active."""
        risk = RiskConfig(circuit_breaker_pct=Decimal("0.05"))
        p = Portfolio(Decimal(10000), risk_config=risk)
        p.process_signal(_buy_signal(), Decimal(100), 1000)
        p.update_equity(Decimal(90))
        assert p.halted is True
        trade = p.force_close(Decimal(90), 2000)
        assert isinstance(trade, Trade)

    def test_resumes_after_recovery(self) -> None:
        """Resume trading when equity recovers by recovery_pct.

        Open a position before the halt triggers. The position's
        mark-to-market value then lifts equity past the recovery target,
        which lifts the halt.
        """
        risk = RiskConfig(
            circuit_breaker_pct=Decimal("0.10"),
            recovery_pct=Decimal("0.05"),
        )
        cfg = ExecutionConfig(position_size_pct=Decimal("0.5"))
        p = Portfolio(Decimal(10000), execution_config=cfg, risk_config=risk)
        # Buy with half capital (5000) at 100 -> qty=50, remaining=5000
        p.process_signal(_buy_signal(), Decimal(100), 1000)
        # Mark price drops to 60: equity=5000 + 50*60=8000, peak=10000, 20%>10%
        p.update_equity(Decimal(60))
        assert p.halted is True
        # halt_equity=8000, recovery target=8000*(1+0.05)=8400
        # Mark price recovers to 70: equity=5000 + 50*70=8500 >= 8400
        p.update_equity(Decimal(70))
        assert p.halted is False

    def test_no_circuit_breaker_by_default(self) -> None:
        """Verify circuit breaker is disabled when not configured."""
        p = Portfolio(Decimal(10000))
        p.process_signal(_buy_signal(), Decimal(100), 1000)
        p.process_signal(_sell_signal(), Decimal(50), 2000)
        p.update_equity(Decimal(50))
        assert p.halted is False


_ATR_PERIOD = 2
_ATR_NEEDED = _ATR_PERIOD + 1


def _candle(
    close: str, *, high: str | None = None, low: str | None = None, ts: int = 1000
) -> Candle:
    """Build a candle for volatility sizing tests."""
    c = Decimal(close)
    h = Decimal(high) if high is not None else c + Decimal(5)
    lo = Decimal(low) if low is not None else c - Decimal(5)
    return Candle(
        symbol="BTC-USD",
        timestamp=ts,
        open=c,
        high=h,
        low=lo,
        close=c,
        volume=Decimal(100),
        interval=Interval.H1,
    )


def _high_vol_history() -> list[Candle]:
    """Build a 3-candle history with high ATR (wide ranges)."""
    return [
        _candle("100", high="120", low="80", ts=1000),
        _candle("105", high="130", low="75", ts=2000),
        _candle("110", high="140", low="70", ts=3000),
    ]


def _low_vol_history() -> list[Candle]:
    """Build a 3-candle history with low ATR (narrow ranges)."""
    return [
        _candle("100", high="101", low="99", ts=1000),
        _candle("100", high="101", low="99", ts=2000),
        _candle("100", high="101", low="99", ts=3000),
    ]


class TestVolatilitySizing:
    """Tests for ATR-based volatility position sizing."""

    def test_reduces_position_in_high_vol(self) -> None:
        """Deploy less capital when ATR is high."""
        cfg = ExecutionConfig(
            volatility_sizing=True,
            atr_period=_ATR_PERIOD,
            target_risk_pct=Decimal("0.02"),
        )
        p = Portfolio(Decimal(10000), execution_config=cfg)
        p.process_signal(_buy_signal(), Decimal(100), 4000, history=_high_vol_history())
        assert p.position is not None
        # Full deployment would be qty=100; vol sizing should reduce it
        assert p.position.quantity < Decimal(100)

    def test_caps_at_position_size_pct(self) -> None:
        """Never exceed position_size_pct even when volatility is very low."""
        cfg = ExecutionConfig(
            position_size_pct=Decimal("0.5"),
            volatility_sizing=True,
            atr_period=_ATR_PERIOD,
            target_risk_pct=Decimal("0.50"),
        )
        p = Portfolio(Decimal(10000), execution_config=cfg)
        p.process_signal(_buy_signal(), Decimal(100), 4000, history=_low_vol_history())
        assert p.position is not None
        # Max allocation is 50% of 10000 = 5000, qty = 5000/100 = 50
        assert p.position.quantity <= Decimal(50)

    def test_falls_back_when_disabled(self) -> None:
        """Use fixed sizing when volatility_sizing is False."""
        cfg = ExecutionConfig(
            position_size_pct=Decimal("0.5"),
            volatility_sizing=False,
        )
        p = Portfolio(Decimal(10000), execution_config=cfg)
        p.process_signal(_buy_signal(), Decimal(100), 4000, history=_high_vol_history())
        assert p.position is not None
        expected_qty = Decimal(50)  # 5000 / 100
        assert p.position.quantity == expected_qty

    def test_falls_back_with_insufficient_history(self) -> None:
        """Use fixed sizing when history is too short for ATR."""
        cfg = ExecutionConfig(
            volatility_sizing=True,
            atr_period=_ATR_PERIOD,
            target_risk_pct=Decimal("0.02"),
        )
        p = Portfolio(Decimal(10000), execution_config=cfg)
        short_history = [_candle("100", ts=1000)]  # Only 1 candle, need 3
        p.process_signal(_buy_signal(), Decimal(100), 4000, history=short_history)
        assert p.position is not None
        # Should fall back to full deployment: qty = 10000/100 = 100
        assert p.position.quantity == Decimal(100)

    def test_falls_back_with_no_history(self) -> None:
        """Use fixed sizing when no history is provided."""
        cfg = ExecutionConfig(
            volatility_sizing=True,
            atr_period=_ATR_PERIOD,
            target_risk_pct=Decimal("0.02"),
        )
        p = Portfolio(Decimal(10000), execution_config=cfg)
        p.process_signal(_buy_signal(), Decimal(100), 4000)
        assert p.position is not None
        assert p.position.quantity == Decimal(100)
