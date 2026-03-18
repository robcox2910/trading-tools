"""Tests for the whale copy trader."""

from __future__ import annotations

import time
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from trading_tools.apps.spread_capture.models import (
    PositionState,
    SideLeg,
    SpreadOpportunity,
    SpreadResult,
)
from trading_tools.apps.whale_copy.config import WhaleCopyConfig
from trading_tools.apps.whale_copy.models import WhalePosition
from trading_tools.apps.whale_copy.signal import WhaleSignalClient
from trading_tools.apps.whale_copy.trader import WhaleCopyTrader
from trading_tools.core.models import ZERO

_DEFAULT_POLL_INTERVAL = 5
_OVERRIDE_POLL_INTERVAL = 10


def _make_opportunity(
    *,
    condition_id: str = "cond_abc",
    window_start: int = 0,
    window_end: int = 300,
) -> SpreadOpportunity:
    """Create a test spread opportunity."""
    return SpreadOpportunity(
        condition_id=condition_id,
        title="Will BTC go up 5m",
        asset="BTC-USD",
        up_token_id="tok_up",
        down_token_id="tok_down",
        up_price=Decimal("0.50"),
        down_price=Decimal("0.50"),
        combined=Decimal("1.00"),
        margin=Decimal("0.00"),
        window_start_ts=window_start,
        window_end_ts=window_end,
    )


def _make_position(
    *,
    whale_side: str | None = "Up",
    up_qty: Decimal = Decimal(10),
    down_qty: Decimal = ZERO,
    window_start: int = 0,
    window_end: int = 300,
) -> WhalePosition:
    """Create a test whale position."""
    opp = _make_opportunity(window_start=window_start, window_end=window_end)
    up_leg = SideLeg("Up", Decimal("0.45"), up_qty, Decimal("0.45") * up_qty)
    down_leg = SideLeg("Down", ZERO, down_qty, ZERO * down_qty)
    return WhalePosition(
        opportunity=opp,
        state=PositionState.ACCUMULATING,
        up_leg=up_leg,
        down_leg=down_leg,
        entry_time=window_start + 10,
        budget=Decimal(100),
        whale_side=whale_side,
    )


class TestComputePnl:
    """Test P&L computation for whale copy positions."""

    def test_winning_side_up(self) -> None:
        """Compute P&L when Up wins and whale bet on Up."""
        config = WhaleCopyConfig(fee_rate=ZERO)
        signal = WhaleSignalClient(whale_addresses=())
        trader = WhaleCopyTrader(config=config, signal_client=signal)

        pos = _make_position(whale_side="Up", up_qty=Decimal(10))

        pnl = trader._compute_pnl(pos, "Up")

        # 10 tokens * $1.00 - cost_basis(10 * 0.45 = 4.50) - 0 fees = 5.50
        assert pnl == Decimal("5.50")

    def test_losing_side(self) -> None:
        """Compute P&L when whale bet wrong — total loss of cost basis."""
        config = WhaleCopyConfig(fee_rate=ZERO)
        signal = WhaleSignalClient(whale_addresses=())
        trader = WhaleCopyTrader(config=config, signal_client=signal)

        pos = _make_position(whale_side="Up", up_qty=Decimal(10))

        pnl = trader._compute_pnl(pos, "Down")

        # Up tokens lose: 0 - 4.50 = -4.50, Down has 0 qty
        assert pnl == Decimal("-4.50")

    def test_both_legs_after_flip(self) -> None:
        """Compute P&L when whale flipped — both legs have tokens."""
        config = WhaleCopyConfig(fee_rate=ZERO)
        signal = WhaleSignalClient(whale_addresses=())
        trader = WhaleCopyTrader(config=config, signal_client=signal)

        opp = _make_opportunity()
        up_leg = SideLeg("Up", Decimal("0.40"), Decimal(10), Decimal("4.00"))
        down_leg = SideLeg("Down", Decimal("0.50"), Decimal(5), Decimal("2.50"))
        pos = WhalePosition(
            opportunity=opp,
            state=PositionState.ACCUMULATING,
            up_leg=up_leg,
            down_leg=down_leg,
            entry_time=10,
            budget=Decimal(100),
            whale_side="Down",
        )

        pnl = trader._compute_pnl(pos, "Up")

        # Up wins: 10 * 1.0 - 4.0 = 6.0, Down loses: 0 - 2.5 = -2.5
        assert pnl == Decimal("3.5")

    def test_unknown_outcome(self) -> None:
        """Compute P&L when outcome is unknown — total loss assumed."""
        config = WhaleCopyConfig(fee_rate=ZERO)
        signal = WhaleSignalClient(whale_addresses=())
        trader = WhaleCopyTrader(config=config, signal_client=signal)

        pos = _make_position(whale_side="Up", up_qty=Decimal(10))

        pnl = trader._compute_pnl(pos, None)

        # All tokens lose: 0 - 4.50 = -4.50
        assert pnl == Decimal("-4.50")

    def test_with_fees(self) -> None:
        """Compute P&L with Polymarket fees deducted."""
        config = WhaleCopyConfig(fee_rate=Decimal("0.25"), fee_exponent=2)
        signal = WhaleSignalClient(whale_addresses=())
        trader = WhaleCopyTrader(config=config, signal_client=signal)

        pos = _make_position(whale_side="Up", up_qty=Decimal(10))

        pnl = trader._compute_pnl(pos, "Up")

        # Fee per token: 0.45 * 0.25 * (0.45 * 0.55)^2 ≈ 0.006891
        # PnL: 10 * 1.0 - 4.50 - 0.06891 ≈ 5.43
        assert pnl > Decimal("5.40")
        assert pnl < Decimal("5.50")


class TestDrawdownHalt:
    """Test drawdown halt behaviour."""

    def test_no_halt_when_no_losses(self) -> None:
        """Return False when no losses have occurred."""
        config = WhaleCopyConfig(max_drawdown_pct=Decimal("0.20"), capital=Decimal(1000))
        signal = WhaleSignalClient(whale_addresses=())
        trader = WhaleCopyTrader(config=config, signal_client=signal)
        trader._session_start_capital = Decimal(1000)

        assert not trader._check_drawdown_halt()

    def test_halt_when_drawdown_exceeded(self) -> None:
        """Return True when cumulative losses exceed max drawdown."""
        config = WhaleCopyConfig(max_drawdown_pct=Decimal("0.10"), capital=Decimal(1000))
        signal = WhaleSignalClient(whale_addresses=())
        trader = WhaleCopyTrader(config=config, signal_client=signal)
        trader._session_start_capital = Decimal(1000)

        # Simulate -$150 losses (15% > 10% max)
        opp = _make_opportunity()
        result = SpreadResult(
            opportunity=opp,
            state=PositionState.SETTLED,
            up_entry=Decimal("0.50"),
            up_qty=Decimal(10),
            down_entry=None,
            down_qty=None,
            total_cost_basis=Decimal(150),
            entry_time=0,
            exit_time=300,
            pnl=Decimal(-150),
        )
        trader._results.append(result)

        assert trader._check_drawdown_halt()


class TestCircuitBreaker:
    """Test circuit breaker behaviour."""

    def test_triggers_after_consecutive_losses(self) -> None:
        """Activate circuit breaker after configured consecutive losses."""
        config = WhaleCopyConfig(circuit_breaker_losses=3, circuit_breaker_cooldown=60)
        signal = WhaleSignalClient(whale_addresses=())
        trader = WhaleCopyTrader(config=config, signal_client=signal)

        trader._record_loss()
        trader._record_loss()
        assert trader._circuit_breaker_until == 0

        trader._record_loss()
        assert trader._circuit_breaker_until > 0


class TestWhalePosition:
    """Test whale position model."""

    def test_total_cost_basis(self) -> None:
        """Return combined cost basis of both legs."""
        pos = _make_position(up_qty=Decimal(10), down_qty=ZERO)
        assert pos.total_cost_basis == Decimal("4.50")

    def test_all_order_ids(self) -> None:
        """Return all order IDs across both legs."""
        pos = _make_position()
        pos.up_leg.order_ids.append("order1")
        pos.down_leg.order_ids.append("order2")
        assert pos.all_order_ids == ["order1", "order2"]


class TestFillPositions:
    """Test the fill logic in the trader."""

    @pytest.mark.asyncio
    async def test_skip_low_conviction(self) -> None:
        """Skip fills when whale conviction is below threshold."""
        config = WhaleCopyConfig(
            min_whale_conviction=Decimal("2.0"),
            max_fill_age_pct=Decimal("0.95"),
        )
        signal = AsyncMock(spec=WhaleSignalClient)
        signal.get_direction = AsyncMock(return_value=("Up", Decimal("1.2")))

        trader = WhaleCopyTrader(config=config, signal_client=signal)

        now = int(time.time())
        pos = _make_position(
            whale_side=None,
            up_qty=ZERO,
            window_start=now - 60,
            window_end=now + 240,
        )
        trader._positions["cond_abc"] = pos

        await trader._fill_positions()

        # No fills should have been made
        assert pos.up_leg.quantity == ZERO
        assert pos.whale_side is None

    @pytest.mark.asyncio
    async def test_skip_past_fill_cutoff(self) -> None:
        """Skip fills when past the fill age cutoff."""
        config = WhaleCopyConfig(max_fill_age_pct=Decimal("0.50"))
        signal = AsyncMock(spec=WhaleSignalClient)
        signal.get_direction = AsyncMock(return_value=("Up", Decimal("3.0")))

        trader = WhaleCopyTrader(config=config, signal_client=signal)

        now = int(time.time())
        # Position started 200s ago in a 300s window → 67% elapsed > 50% cutoff
        pos = _make_position(
            whale_side=None,
            up_qty=ZERO,
            window_start=now - 200,
            window_end=now + 100,
        )
        trader._positions["cond_abc"] = pos

        await trader._fill_positions()

        # Signal should not even be queried for this position
        assert pos.up_leg.quantity == ZERO


class TestConfig:
    """Test WhaleCopyConfig construction and overrides."""

    def test_defaults(self) -> None:
        """Verify sensible default values."""
        config = WhaleCopyConfig()
        assert config.poll_interval == _DEFAULT_POLL_INTERVAL
        assert config.capital == Decimal(1000)
        assert config.fill_size_tokens == Decimal(5)
        assert config.min_whale_conviction == Decimal("1.5")

    def test_with_overrides(self) -> None:
        """Apply overrides to a base config."""
        base = WhaleCopyConfig()
        config = WhaleCopyConfig.with_overrides(
            base,
            capital=Decimal(500),
            poll_interval=_OVERRIDE_POLL_INTERVAL,
            unknown_key="ignored",
        )
        assert config.capital == Decimal(500)
        assert config.poll_interval == _OVERRIDE_POLL_INTERVAL
        # Other fields unchanged
        assert config.fill_size_tokens == Decimal(5)

    def test_none_overrides_skipped(self) -> None:
        """Skip None values in overrides."""
        base = WhaleCopyConfig(capital=Decimal(2000))
        config = WhaleCopyConfig.with_overrides(base, capital=None)
        assert config.capital == Decimal(2000)
