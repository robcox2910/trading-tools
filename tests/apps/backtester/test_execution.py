"""Tests for shared execution helpers."""

from decimal import Decimal

import pytest
import typer

from trading_tools.apps.backtester.cli._helpers import build_execution_config
from trading_tools.apps.backtester.execution import (
    apply_entry_slippage,
    apply_exit_slippage,
    check_risk_triggers,
    compute_allocation,
)
from trading_tools.core.models import (
    ZERO,
    Candle,
    ExecutionConfig,
    Interval,
    RiskConfig,
    Side,
)

_ATR_PERIOD = 2
_ATR_NEEDED = _ATR_PERIOD + 1


def _candle(
    close: str,
    *,
    high: str | None = None,
    low: str | None = None,
    ts: int = 1000,
) -> Candle:
    """Build a candle for execution helper tests."""
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


class TestApplySlippage:
    """Tests for entry and exit slippage helpers."""

    def test_entry_slippage_increases_price(self) -> None:
        """Verify entry slippage worsens the buy price upward."""
        result = apply_entry_slippage(Decimal(100), Decimal("0.01"))
        assert result == Decimal(101)

    def test_exit_slippage_decreases_price(self) -> None:
        """Verify exit slippage worsens the sell price downward."""
        result = apply_exit_slippage(Decimal(100), Decimal("0.01"))
        assert result == Decimal(99)

    def test_zero_slippage_preserves_price(self) -> None:
        """Verify zero slippage leaves the price unchanged."""
        assert apply_entry_slippage(Decimal(100), ZERO) == Decimal(100)
        assert apply_exit_slippage(Decimal(100), ZERO) == Decimal(100)


class TestComputeAllocation:
    """Tests for position allocation computation."""

    def test_full_deployment(self) -> None:
        """Verify default config deploys all capital."""
        cfg = ExecutionConfig()
        allocation, entry_fee, quantity = compute_allocation(
            capital=Decimal(10000),
            price=Decimal(100),
            exec_config=cfg,
        )
        assert allocation == Decimal(10000)
        assert entry_fee == ZERO
        assert quantity == Decimal(100)

    def test_half_position_size(self) -> None:
        """Verify position_size_pct=0.5 deploys half the capital."""
        cfg = ExecutionConfig(position_size_pct=Decimal("0.5"))
        allocation, _fee, quantity = compute_allocation(
            capital=Decimal(10000),
            price=Decimal(100),
            exec_config=cfg,
        )
        assert allocation == Decimal(5000)
        assert quantity == Decimal(50)

    def test_with_fees(self) -> None:
        """Verify taker fees are deducted from the investable amount."""
        cfg = ExecutionConfig(taker_fee_pct=Decimal("0.001"))
        allocation, entry_fee, quantity = compute_allocation(
            capital=Decimal(10000),
            price=Decimal(100),
            exec_config=cfg,
        )
        assert allocation == Decimal(10000)
        assert entry_fee == Decimal(10)
        assert quantity == Decimal("99.9")

    def test_volatility_sizing_reduces_allocation(self) -> None:
        """Verify ATR-based sizing reduces position in high-vol conditions."""
        cfg = ExecutionConfig(
            volatility_sizing=True,
            atr_period=_ATR_PERIOD,
            target_risk_pct=Decimal("0.02"),
        )
        allocation, _fee, quantity = compute_allocation(
            capital=Decimal(10000),
            price=Decimal(100),
            exec_config=cfg,
            history=_high_vol_history(),
        )
        # Full deployment would be 10000; vol sizing should reduce it
        assert allocation < Decimal(10000)
        assert quantity < Decimal(100)

    def test_volatility_sizing_caps_at_max(self) -> None:
        """Verify vol sizing never exceeds position_size_pct cap."""
        cfg = ExecutionConfig(
            position_size_pct=Decimal("0.5"),
            volatility_sizing=True,
            atr_period=_ATR_PERIOD,
            target_risk_pct=Decimal("0.50"),
        )
        allocation, _fee, _qty = compute_allocation(
            capital=Decimal(10000),
            price=Decimal(100),
            exec_config=cfg,
            history=_low_vol_history(),
        )
        # Max is 50% of 10000 = 5000
        assert allocation <= Decimal(5000)

    def test_falls_back_with_insufficient_history(self) -> None:
        """Verify fixed sizing when history too short for ATR."""
        cfg = ExecutionConfig(
            volatility_sizing=True,
            atr_period=_ATR_PERIOD,
            target_risk_pct=Decimal("0.02"),
        )
        short_history = [_candle("100", ts=1000)]
        allocation, _fee, quantity = compute_allocation(
            capital=Decimal(10000),
            price=Decimal(100),
            exec_config=cfg,
            history=short_history,
        )
        assert allocation == Decimal(10000)
        assert quantity == Decimal(100)

    def test_zero_price_returns_zeros(self) -> None:
        """Return zeros when price is zero to avoid ZeroDivisionError."""
        cfg = ExecutionConfig()
        allocation, entry_fee, quantity = compute_allocation(
            capital=Decimal(10000),
            price=ZERO,
            exec_config=cfg,
        )
        assert allocation == ZERO
        assert entry_fee == ZERO
        assert quantity == ZERO

    def test_negative_price_returns_zeros(self) -> None:
        """Return zeros when price is negative."""
        cfg = ExecutionConfig()
        allocation, entry_fee, quantity = compute_allocation(
            capital=Decimal(10000),
            price=Decimal(-100),
            exec_config=cfg,
        )
        assert allocation == ZERO
        assert entry_fee == ZERO
        assert quantity == ZERO


class TestCheckRiskTriggers:
    """Tests for stop-loss and take-profit trigger detection."""

    def test_stop_loss_triggers(self) -> None:
        """Verify stop-loss triggers when candle low breaches threshold."""
        risk = RiskConfig(stop_loss_pct=Decimal("0.05"))
        candle = _candle("95", low="93")
        entry_price = Decimal(100)
        result = check_risk_triggers(candle, entry_price, risk)
        assert result == Decimal(95)  # 100 * (1 - 0.05)

    def test_take_profit_triggers(self) -> None:
        """Verify take-profit triggers when candle high breaches threshold."""
        risk = RiskConfig(take_profit_pct=Decimal("0.10"))
        candle = _candle("108", high="112")
        entry_price = Decimal(100)
        result = check_risk_triggers(candle, entry_price, risk)
        assert result == Decimal(110)  # 100 * (1 + 0.10)

    def test_stop_loss_priority_over_take_profit(self) -> None:
        """Verify stop-loss takes priority when both trigger on same candle."""
        risk = RiskConfig(
            stop_loss_pct=Decimal("0.05"),
            take_profit_pct=Decimal("0.10"),
        )
        candle = _candle("100", high="115", low="90")
        entry_price = Decimal(100)
        result = check_risk_triggers(candle, entry_price, risk)
        assert result == Decimal(95)  # stop-loss wins

    def test_no_trigger_returns_none(self) -> None:
        """Verify None returned when neither threshold is breached."""
        risk = RiskConfig(
            stop_loss_pct=Decimal("0.05"),
            take_profit_pct=Decimal("0.10"),
        )
        candle = _candle("102", high="104", low="98")
        entry_price = Decimal(100)
        result = check_risk_triggers(candle, entry_price, risk)
        assert result is None

    def test_no_risk_config_returns_none(self) -> None:
        """Verify None when both stop-loss and take-profit are None."""
        risk = RiskConfig()
        candle = _candle("50", high="200", low="1")
        entry_price = Decimal(100)
        result = check_risk_triggers(candle, entry_price, risk)
        assert result is None

    def test_short_stop_loss_triggers_on_high(self) -> None:
        """Verify SHORT stop-loss triggers when candle high breaches threshold."""
        risk = RiskConfig(stop_loss_pct=Decimal("0.05"))
        candle = _candle("103", high="106")
        entry_price = Decimal(100)
        result = check_risk_triggers(candle, entry_price, risk, side=Side.SELL)
        assert result == Decimal(105)  # 100 * (1 + 0.05)

    def test_short_take_profit_triggers_on_low(self) -> None:
        """Verify SHORT take-profit triggers when candle low breaches threshold."""
        risk = RiskConfig(take_profit_pct=Decimal("0.10"))
        candle = _candle("92", low="88")
        entry_price = Decimal(100)
        result = check_risk_triggers(candle, entry_price, risk, side=Side.SELL)
        assert result == Decimal(90)  # 100 * (1 - 0.10)

    def test_short_no_trigger_returns_none(self) -> None:
        """Verify None for SHORT when neither threshold is breached."""
        risk = RiskConfig(
            stop_loss_pct=Decimal("0.05"),
            take_profit_pct=Decimal("0.10"),
        )
        candle = _candle("98", high="102", low="96")
        entry_price = Decimal(100)
        result = check_risk_triggers(candle, entry_price, risk, side=Side.SELL)
        assert result is None


class TestBuildExecutionConfigValidation:
    """Tests for CLI execution config bounds validation."""

    def test_negative_maker_fee_raises(self) -> None:
        """Reject negative maker fee."""
        with pytest.raises(typer.BadParameter, match="maker-fee"):
            build_execution_config(
                maker_fee=-0.01,
                taker_fee=0.0,
                slippage=0.0,
                position_size=1.0,
            )

    def test_negative_taker_fee_raises(self) -> None:
        """Reject negative taker fee."""
        with pytest.raises(typer.BadParameter, match="taker-fee"):
            build_execution_config(
                maker_fee=0.0,
                taker_fee=-0.01,
                slippage=0.0,
                position_size=1.0,
            )

    def test_slippage_above_one_raises(self) -> None:
        """Reject slippage > 1."""
        with pytest.raises(typer.BadParameter, match="slippage"):
            build_execution_config(
                maker_fee=0.0,
                taker_fee=0.0,
                slippage=1.5,
                position_size=1.0,
            )

    def test_position_size_zero_raises(self) -> None:
        """Reject position size of 0."""
        with pytest.raises(typer.BadParameter, match="position-size"):
            build_execution_config(
                maker_fee=0.0,
                taker_fee=0.0,
                slippage=0.0,
                position_size=0.0,
            )

    def test_position_size_above_one_raises(self) -> None:
        """Reject position size > 1."""
        with pytest.raises(typer.BadParameter, match="position-size"):
            build_execution_config(
                maker_fee=0.0,
                taker_fee=0.0,
                slippage=0.0,
                position_size=1.5,
            )

    def test_valid_params_succeed(self) -> None:
        """Accept valid parameters without raising."""
        config = build_execution_config(
            maker_fee=0.001,
            taker_fee=0.002,
            slippage=0.01,
            position_size=0.5,
        )
        assert config.maker_fee_pct == Decimal("0.001")
        assert config.position_size_pct == Decimal("0.5")
