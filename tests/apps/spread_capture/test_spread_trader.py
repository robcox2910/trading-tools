"""Tests for SpreadTrader core trading engine."""

from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trading_tools.apps.spread_capture.config import SpreadCaptureConfig
from trading_tools.apps.spread_capture.models import (
    PairedPosition,
    PositionState,
    SideLeg,
    SpreadOpportunity,
    SpreadResult,
)
from trading_tools.apps.spread_capture.spread_trader import SpreadTrader

_UP_PRICE = Decimal("0.48")
_DOWN_PRICE = Decimal("0.47")
_COMBINED = _UP_PRICE + _DOWN_PRICE
_MARGIN = Decimal(1) - _COMBINED
_QTY = Decimal(10)
_CAPITAL = Decimal(100)
_MAX_POS_PCT = Decimal("0.10")
_WINDOW_START = 1_710_000_000
_WINDOW_END = 1_710_000_300
_NOW = 1_710_000_100
_PAST_TS = 1_709_999_000
_DEFAULT_POLL = 5
_ZERO_FEE = Decimal("0.0")
_DEFAULT_FEE_EXPONENT = 2


def _make_config(**overrides: Any) -> SpreadCaptureConfig:
    """Create a SpreadCaptureConfig with test defaults."""
    defaults: dict[str, Any] = {
        "capital": _CAPITAL,
        "max_position_pct": _MAX_POS_PCT,
        "max_combined_cost": Decimal("0.98"),
        "min_spread_margin": Decimal("0.01"),
        "max_open_positions": 10,
        "poll_interval": _DEFAULT_POLL,
        "paper_slippage_pct": Decimal("0.0"),
        "circuit_breaker_losses": 3,
        "circuit_breaker_cooldown": 300,
        "max_drawdown_pct": Decimal("0.15"),
        "compound_profits": True,
        "fee_rate": _ZERO_FEE,
        "fee_exponent": _DEFAULT_FEE_EXPONENT,
    }
    defaults.update(overrides)
    return SpreadCaptureConfig(**defaults)


def _make_opportunity(**overrides: Any) -> SpreadOpportunity:
    """Create a SpreadOpportunity with sensible defaults."""
    defaults: dict[str, Any] = {
        "condition_id": "cond_a",
        "title": "Bitcoin Up or Down?",
        "asset": "BTC-USD",
        "up_token_id": "up_tok_1",
        "down_token_id": "down_tok_1",
        "up_price": _UP_PRICE,
        "down_price": _DOWN_PRICE,
        "combined": _COMBINED,
        "margin": _MARGIN,
        "window_start_ts": _WINDOW_START,
        "window_end_ts": _WINDOW_END,
    }
    defaults.update(overrides)
    return SpreadOpportunity(**defaults)


def _make_trader(**overrides: Any) -> SpreadTrader:
    """Create a SpreadTrader with mock client and sensible defaults."""
    config = overrides.pop("config", _make_config())
    client = overrides.pop("client", AsyncMock())
    return SpreadTrader(config=config, live=False, client=client, **overrides)


@pytest.mark.asyncio
class TestSpreadTraderEntry:
    """Test spread entry logic."""

    async def test_paper_entry_creates_paired_position(self) -> None:
        """Paper entry creates a PAIRED position with both legs."""
        trader = _make_trader()
        opp = _make_opportunity()

        with patch("trading_tools.apps.spread_capture.spread_trader.time") as mock_time:
            mock_time.time.return_value = _NOW
            await trader._enter_spread(opp)

        assert "cond_a" in trader._positions
        pos = trader._positions["cond_a"]
        assert pos.state == PositionState.PAIRED
        assert pos.up_leg.side == "Up"
        assert pos.down_leg is not None
        assert pos.down_leg.side == "Down"
        assert pos.is_paper is True

    async def test_position_sizing(self) -> None:
        """Quantity = (capital * max_position_pct) / combined cost."""
        trader = _make_trader()
        opp = _make_opportunity()

        with patch("trading_tools.apps.spread_capture.spread_trader.time") as mock_time:
            mock_time.time.return_value = _NOW
            await trader._enter_spread(opp)

        pos = trader._positions["cond_a"]
        expected_spend = _CAPITAL * _MAX_POS_PCT  # 10
        expected_qty = (expected_spend / _COMBINED).quantize(Decimal("0.01"))
        assert pos.up_leg.quantity == expected_qty

    async def test_skips_below_min_qty(self) -> None:
        """Skip entry when computed quantity is below minimum (5 tokens)."""
        config = _make_config(capital=Decimal(1), max_position_pct=Decimal("0.10"))
        trader = _make_trader(config=config)
        opp = _make_opportunity()

        with patch("trading_tools.apps.spread_capture.spread_trader.time") as mock_time:
            mock_time.time.return_value = _NOW
            await trader._enter_spread(opp)

        assert "cond_a" not in trader._positions

    async def test_max_open_positions_cap(self) -> None:
        """Don't enter when max_open_positions is reached."""
        config = _make_config(max_open_positions=1)
        trader = _make_trader(config=config)

        # Pre-fill one position
        opp1 = _make_opportunity(condition_id="cond_existing")
        up_leg = SideLeg(
            side="Up", entry_price=_UP_PRICE, quantity=_QTY, cost_basis=_UP_PRICE * _QTY
        )
        down_leg = SideLeg(
            side="Down", entry_price=_DOWN_PRICE, quantity=_QTY, cost_basis=_DOWN_PRICE * _QTY
        )
        trader._positions["cond_existing"] = PairedPosition(
            opportunity=opp1,
            state=PositionState.PAIRED,
            up_leg=up_leg,
            down_leg=down_leg,
            entry_time=_NOW,
        )

        # Set up scanner mock
        scanner = MagicMock()
        scanner.scan = AsyncMock(return_value=[_make_opportunity(condition_id="cond_new")])
        trader._scanner = scanner

        with patch("trading_tools.apps.spread_capture.spread_trader.time") as mock_time:
            mock_time.time.return_value = _NOW
            mock_time.monotonic.return_value = 100.0
            await trader._poll_cycle()

        assert "cond_new" not in trader._positions

    async def test_paper_slippage_worsens_price(self) -> None:
        """Paper slippage increases both entry prices."""
        slippage = Decimal("0.01")
        config = _make_config(paper_slippage_pct=slippage)
        trader = _make_trader(config=config)
        opp = _make_opportunity()

        with patch("trading_tools.apps.spread_capture.spread_trader.time") as mock_time:
            mock_time.time.return_value = _NOW
            await trader._enter_spread(opp)

        pos = trader._positions["cond_a"]
        expected_up = _UP_PRICE * (Decimal(1) + slippage)
        assert pos.up_leg.entry_price == expected_up


@pytest.mark.asyncio
class TestSpreadTraderSettlement:
    """Test position settlement logic."""

    async def test_paired_position_settles_with_profit(self) -> None:
        """PAIRED position settles with guaranteed profit (winning_qty - cost)."""
        config = _make_config(fee_rate=_ZERO_FEE)
        trader = _make_trader(config=config)
        opp = _make_opportunity(window_end_ts=_NOW - 1)
        up_leg = SideLeg(
            side="Up", entry_price=_UP_PRICE, quantity=_QTY, cost_basis=_UP_PRICE * _QTY
        )
        down_leg = SideLeg(
            side="Down", entry_price=_DOWN_PRICE, quantity=_QTY, cost_basis=_DOWN_PRICE * _QTY
        )
        trader._positions["cond_a"] = PairedPosition(
            opportunity=opp,
            state=PositionState.PAIRED,
            up_leg=up_leg,
            down_leg=down_leg,
            entry_time=_PAST_TS,
        )

        # Mock outcome resolution
        with (
            patch.object(trader, "_resolve_outcome", return_value="Up"),
            patch("trading_tools.apps.spread_capture.spread_trader.time") as mock_time,
        ):
            mock_time.time.return_value = _NOW
            await trader._settle_expired_positions()

        assert "cond_a" not in trader._positions
        assert len(trader._results) == 1
        result = trader._results[0]
        # P&L = winning_qty * 1.0 - total_cost = 10 * 1.0 - (4.8 + 4.7) = 0.5
        expected_pnl = _QTY * Decimal(1) - (_UP_PRICE * _QTY + _DOWN_PRICE * _QTY)
        assert result.pnl == expected_pnl
        assert result.pnl > Decimal(0)

    async def test_single_leg_win(self) -> None:
        """SINGLE_LEG position profits when the single leg wins."""
        config = _make_config(fee_rate=_ZERO_FEE)
        trader = _make_trader(config=config)
        opp = _make_opportunity(window_end_ts=_NOW - 1)
        up_leg = SideLeg(
            side="Up", entry_price=_UP_PRICE, quantity=_QTY, cost_basis=_UP_PRICE * _QTY
        )
        trader._positions["cond_a"] = PairedPosition(
            opportunity=opp,
            state=PositionState.SINGLE_LEG,
            up_leg=up_leg,
            down_leg=None,
            entry_time=_PAST_TS,
        )

        with (
            patch.object(trader, "_resolve_outcome", return_value="Up"),
            patch("trading_tools.apps.spread_capture.spread_trader.time") as mock_time,
        ):
            mock_time.time.return_value = _NOW
            await trader._settle_expired_positions()

        result = trader._results[0]
        # P&L = qty * 1.0 - cost = 10 * 1.0 - 4.8 = 5.2
        expected_pnl = _QTY * Decimal(1) - _UP_PRICE * _QTY
        assert result.pnl == expected_pnl

    async def test_single_leg_loss(self) -> None:
        """SINGLE_LEG position loses when the single leg loses."""
        config = _make_config(fee_rate=_ZERO_FEE)
        trader = _make_trader(config=config)
        opp = _make_opportunity(window_end_ts=_NOW - 1)
        up_leg = SideLeg(
            side="Up", entry_price=_UP_PRICE, quantity=_QTY, cost_basis=_UP_PRICE * _QTY
        )
        trader._positions["cond_a"] = PairedPosition(
            opportunity=opp,
            state=PositionState.SINGLE_LEG,
            up_leg=up_leg,
            down_leg=None,
            entry_time=_PAST_TS,
        )

        with (
            patch.object(trader, "_resolve_outcome", return_value="Down"),
            patch("trading_tools.apps.spread_capture.spread_trader.time") as mock_time,
        ):
            mock_time.time.return_value = _NOW
            await trader._settle_expired_positions()

        result = trader._results[0]
        # P&L = 0 - cost = -4.8
        assert result.pnl == Decimal(0) - _UP_PRICE * _QTY

    async def test_unknown_outcome_paired(self) -> None:
        """PAIRED position with unknown outcome uses conservative estimate."""
        config = _make_config(fee_rate=_ZERO_FEE)
        trader = _make_trader(config=config)
        opp = _make_opportunity(window_end_ts=_NOW - 1)
        up_leg = SideLeg(
            side="Up", entry_price=_UP_PRICE, quantity=_QTY, cost_basis=_UP_PRICE * _QTY
        )
        down_leg = SideLeg(
            side="Down", entry_price=_DOWN_PRICE, quantity=_QTY, cost_basis=_DOWN_PRICE * _QTY
        )
        trader._positions["cond_a"] = PairedPosition(
            opportunity=opp,
            state=PositionState.PAIRED,
            up_leg=up_leg,
            down_leg=down_leg,
            entry_time=_PAST_TS,
        )

        with (
            patch.object(trader, "_resolve_outcome", return_value=None),
            patch("trading_tools.apps.spread_capture.spread_trader.time") as mock_time,
        ):
            mock_time.time.return_value = _NOW
            await trader._settle_expired_positions()

        result = trader._results[0]
        # Unknown but paired: use min qty. min(10, 10) * 1.0 - 9.5 = 0.5
        assert result.pnl > Decimal(0)
        assert result.outcome_known is False

    async def test_settle_skips_pending_positions(self) -> None:
        """PENDING positions are not settled -- they are managed separately."""
        trader = _make_trader()
        opp = _make_opportunity(window_end_ts=_NOW - 1)
        up_leg = SideLeg(
            side="Up", entry_price=_UP_PRICE, quantity=_QTY, cost_basis=_UP_PRICE * _QTY
        )
        down_leg = SideLeg(
            side="Down", entry_price=_DOWN_PRICE, quantity=_QTY, cost_basis=_DOWN_PRICE * _QTY
        )
        trader._positions["cond_a"] = PairedPosition(
            opportunity=opp,
            state=PositionState.PENDING,
            up_leg=up_leg,
            down_leg=down_leg,
            entry_time=_PAST_TS,
            pending_up_order_id="order_up",
            pending_down_order_id="order_down",
        )

        with patch("trading_tools.apps.spread_capture.spread_trader.time") as mock_time:
            mock_time.time.return_value = _NOW
            await trader._settle_expired_positions()

        # Position should still be there -- not settled
        assert "cond_a" in trader._positions
        assert len(trader._results) == 0


@pytest.mark.asyncio
class TestPendingOrderManagement:
    """Test GTC limit order pending state management."""

    async def test_both_filled_transitions_to_paired(self) -> None:
        """When both GTC orders are filled, position transitions to PAIRED."""
        client = AsyncMock()
        # No open orders -- both filled
        client.get_open_orders = AsyncMock(return_value=[])
        trader = _make_trader(client=client)

        opp = _make_opportunity()
        up_leg = SideLeg(
            side="Up", entry_price=_UP_PRICE, quantity=_QTY, cost_basis=_UP_PRICE * _QTY
        )
        down_leg = SideLeg(
            side="Down", entry_price=_DOWN_PRICE, quantity=_QTY, cost_basis=_DOWN_PRICE * _QTY
        )
        trader._positions["cond_a"] = PairedPosition(
            opportunity=opp,
            state=PositionState.PENDING,
            up_leg=up_leg,
            down_leg=down_leg,
            entry_time=_NOW,
            is_paper=False,
            pending_up_order_id="order_up_1",
            pending_down_order_id="order_down_1",
        )

        with patch("trading_tools.apps.spread_capture.spread_trader.time") as mock_time:
            mock_time.time.return_value = _NOW
            await trader._manage_pending_orders()

        pos = trader._positions["cond_a"]
        assert pos.state == PositionState.PAIRED
        assert pos.pending_up_order_id is None
        assert pos.pending_down_order_id is None

    async def test_timeout_one_side_unfilled_becomes_single_leg(self) -> None:
        """When one side times out, cancel unfilled and mark SINGLE_LEG."""
        client = AsyncMock()
        # Down order still open (unfilled)
        open_order = MagicMock()
        open_order.order_id = "order_down_1"
        client.get_open_orders = AsyncMock(return_value=[open_order])
        client.cancel_order = AsyncMock()
        config = _make_config(single_leg_timeout=10)
        trader = _make_trader(config=config, client=client)

        opp = _make_opportunity()
        up_leg = SideLeg(
            side="Up", entry_price=_UP_PRICE, quantity=_QTY, cost_basis=_UP_PRICE * _QTY
        )
        down_leg = SideLeg(
            side="Down", entry_price=_DOWN_PRICE, quantity=_QTY, cost_basis=_DOWN_PRICE * _QTY
        )
        # entry_time is 15 seconds ago (> 10s timeout)
        trader._positions["cond_a"] = PairedPosition(
            opportunity=opp,
            state=PositionState.PENDING,
            up_leg=up_leg,
            down_leg=down_leg,
            entry_time=_NOW - 15,
            is_paper=False,
            pending_up_order_id="order_up_1",
            pending_down_order_id="order_down_1",
        )

        with patch("trading_tools.apps.spread_capture.spread_trader.time") as mock_time:
            mock_time.time.return_value = _NOW
            await trader._manage_pending_orders()

        pos = trader._positions["cond_a"]
        assert pos.state == PositionState.SINGLE_LEG
        assert pos.down_leg is None
        # Down order should have been cancelled
        client.cancel_order.assert_called_once_with("order_down_1")

    async def test_timeout_both_unfilled_removes_position(self) -> None:
        """When both sides time out, cancel both and remove position."""
        client = AsyncMock()
        # Both orders still open
        open_up = MagicMock()
        open_up.order_id = "order_up_1"
        open_down = MagicMock()
        open_down.order_id = "order_down_1"
        client.get_open_orders = AsyncMock(return_value=[open_up, open_down])
        client.cancel_order = AsyncMock()
        config = _make_config(single_leg_timeout=10)
        trader = _make_trader(config=config, client=client)

        opp = _make_opportunity()
        up_leg = SideLeg(
            side="Up", entry_price=_UP_PRICE, quantity=_QTY, cost_basis=_UP_PRICE * _QTY
        )
        down_leg = SideLeg(
            side="Down", entry_price=_DOWN_PRICE, quantity=_QTY, cost_basis=_DOWN_PRICE * _QTY
        )
        trader._positions["cond_a"] = PairedPosition(
            opportunity=opp,
            state=PositionState.PENDING,
            up_leg=up_leg,
            down_leg=down_leg,
            entry_time=_NOW - 15,
            is_paper=False,
            pending_up_order_id="order_up_1",
            pending_down_order_id="order_down_1",
        )

        with patch("trading_tools.apps.spread_capture.spread_trader.time") as mock_time:
            mock_time.time.return_value = _NOW
            await trader._manage_pending_orders()

        assert "cond_a" not in trader._positions

    async def test_expired_market_cancels_pending(self) -> None:
        """Pending orders are cancelled when market expires."""
        client = AsyncMock()
        open_up = MagicMock()
        open_up.order_id = "order_up_1"
        open_down = MagicMock()
        open_down.order_id = "order_down_1"
        client.get_open_orders = AsyncMock(return_value=[open_up, open_down])
        client.cancel_order = AsyncMock()
        trader = _make_trader(client=client)

        # Market already expired
        opp = _make_opportunity(window_end_ts=_NOW - 1)
        up_leg = SideLeg(
            side="Up", entry_price=_UP_PRICE, quantity=_QTY, cost_basis=_UP_PRICE * _QTY
        )
        down_leg = SideLeg(
            side="Down", entry_price=_DOWN_PRICE, quantity=_QTY, cost_basis=_DOWN_PRICE * _QTY
        )
        trader._positions["cond_a"] = PairedPosition(
            opportunity=opp,
            state=PositionState.PENDING,
            up_leg=up_leg,
            down_leg=down_leg,
            entry_time=_PAST_TS,
            is_paper=False,
            pending_up_order_id="order_up_1",
            pending_down_order_id="order_down_1",
        )

        with patch("trading_tools.apps.spread_capture.spread_trader.time") as mock_time:
            mock_time.time.return_value = _NOW
            await trader._manage_pending_orders()

        assert "cond_a" not in trader._positions

    async def test_no_pending_positions_is_noop(self) -> None:
        """No-op when there are no PENDING positions."""
        client = AsyncMock()
        trader = _make_trader(client=client)

        await trader._manage_pending_orders()

        # get_open_orders should not have been called
        client.get_open_orders.assert_not_called()


@pytest.mark.asyncio
class TestCapitalManagement:
    """Test capital and risk management."""

    async def test_compound_profits(self) -> None:
        """Capital grows with realised P&L when compound_profits is enabled."""
        trader = _make_trader()
        # Add a profitable result
        opp = _make_opportunity()
        trader._results.append(
            SpreadResult(
                opportunity=opp,
                state=PositionState.SETTLED,
                up_entry=_UP_PRICE,
                up_qty=_QTY,
                down_entry=_DOWN_PRICE,
                down_qty=_QTY,
                total_cost_basis=_COMBINED * _QTY,
                entry_time=_NOW,
                exit_time=_NOW + 300,
                pnl=Decimal("0.50"),
            )
        )

        capital = trader._get_capital()
        assert capital == _CAPITAL + Decimal("0.50")

    async def test_drawdown_halt(self) -> None:
        """Drawdown halt prevents new entries when losses exceed threshold."""
        config = _make_config(max_drawdown_pct=Decimal("0.10"))
        trader = _make_trader(config=config)
        trader._session_start_capital = _CAPITAL

        # Add a loss exceeding 10% of capital
        opp = _make_opportunity()
        trader._results.append(
            SpreadResult(
                opportunity=opp,
                state=PositionState.SETTLED,
                up_entry=_UP_PRICE,
                up_qty=_QTY,
                down_entry=None,
                down_qty=None,
                total_cost_basis=_UP_PRICE * _QTY,
                entry_time=_NOW,
                exit_time=_NOW + 300,
                pnl=Decimal("-11.00"),
            )
        )

        assert trader._check_drawdown_halt() is True

    async def test_circuit_breaker_activates(self) -> None:
        """Circuit breaker activates after consecutive losses."""
        config = _make_config(circuit_breaker_losses=2, circuit_breaker_cooldown=60)
        trader = _make_trader(config=config)

        with patch("trading_tools.apps.spread_capture.spread_trader.time") as mock_time:
            mock_time.time.return_value = _NOW
            trader._record_loss()
            trader._record_loss()

        assert trader._circuit_breaker_until == _NOW + 60

    async def test_circuit_breaker_skips_entry(self) -> None:
        """Active circuit breaker prevents new entries."""
        trader = _make_trader()
        trader._circuit_breaker_until = _NOW + 100
        opp = _make_opportunity()

        with patch("trading_tools.apps.spread_capture.spread_trader.time") as mock_time:
            mock_time.time.return_value = _NOW
            await trader._enter_spread(opp)

        assert "cond_a" not in trader._positions


@pytest.mark.asyncio
class TestDatabasePersistence:
    """Test that results are persisted to the repository."""

    async def test_persist_result_on_settle(self) -> None:
        """Settled positions are persisted via the repository."""
        config = _make_config(fee_rate=_ZERO_FEE)
        trader = _make_trader(config=config)
        mock_repo = AsyncMock()
        trader.set_repo(mock_repo)

        opp = _make_opportunity(window_end_ts=_NOW - 1)
        up_leg = SideLeg(
            side="Up", entry_price=_UP_PRICE, quantity=_QTY, cost_basis=_UP_PRICE * _QTY
        )
        down_leg = SideLeg(
            side="Down", entry_price=_DOWN_PRICE, quantity=_QTY, cost_basis=_DOWN_PRICE * _QTY
        )
        trader._positions["cond_a"] = PairedPosition(
            opportunity=opp,
            state=PositionState.PAIRED,
            up_leg=up_leg,
            down_leg=down_leg,
            entry_time=_PAST_TS,
        )

        with (
            patch.object(trader, "_resolve_outcome", return_value="Up"),
            patch("trading_tools.apps.spread_capture.spread_trader.time") as mock_time,
        ):
            mock_time.time.return_value = _NOW
            await trader._settle_expired_positions()

        mock_repo.save_result.assert_called_once()


@pytest.mark.asyncio
class TestHeartbeat:
    """Test heartbeat logging."""

    async def test_heartbeat_logs_metrics(self) -> None:
        """Heartbeat logger emits metrics when interval elapses."""
        trader = _make_trader()
        trader._scanner = MagicMock()
        trader._scanner.known_market_count = 5

        with patch.object(trader._heartbeat, "maybe_log") as mock_log:
            trader._log_heartbeat()
            mock_log.assert_called_once()
            kwargs = mock_log.call_args.kwargs
            assert "polls" in kwargs
            assert "known_markets" in kwargs


@pytest.mark.asyncio
class TestFeeAdjustedPnl:
    """Test that P&L calculation deducts Polymarket entry fees."""

    async def test_paired_pnl_deducts_fees(self) -> None:
        """Paired settlement deducts Polymarket entry fees from P&L."""
        config = _make_config(fee_rate=Decimal("0.25"), fee_exponent=2)
        trader = _make_trader(config=config)
        opp = _make_opportunity(window_end_ts=_NOW - 1)
        up_leg = SideLeg(
            side="Up", entry_price=_UP_PRICE, quantity=_QTY, cost_basis=_UP_PRICE * _QTY
        )
        down_leg = SideLeg(
            side="Down", entry_price=_DOWN_PRICE, quantity=_QTY, cost_basis=_DOWN_PRICE * _QTY
        )
        trader._positions["cond_a"] = PairedPosition(
            opportunity=opp,
            state=PositionState.PAIRED,
            up_leg=up_leg,
            down_leg=down_leg,
            entry_time=_PAST_TS,
        )

        with (
            patch.object(trader, "_resolve_outcome", return_value="Up"),
            patch("trading_tools.apps.spread_capture.spread_trader.time") as mock_time,
        ):
            mock_time.time.return_value = _NOW
            await trader._settle_expired_positions()

        result = trader._results[0]
        # Raw P&L without fees = 10 * 1.0 - (4.8 + 4.7) = 0.5
        raw_pnl = _QTY * Decimal(1) - (_UP_PRICE * _QTY + _DOWN_PRICE * _QTY)
        # With fees deducted, P&L should be less than raw
        assert result.pnl < raw_pnl
        # But should still be positive (fees are small relative to margin)
        assert result.pnl > Decimal(0)

    async def test_zero_fee_rate_no_deduction(self) -> None:
        """Zero fee rate preserves raw P&L exactly."""
        config = _make_config(fee_rate=_ZERO_FEE)
        trader = _make_trader(config=config)
        opp = _make_opportunity(window_end_ts=_NOW - 1)
        up_leg = SideLeg(
            side="Up", entry_price=_UP_PRICE, quantity=_QTY, cost_basis=_UP_PRICE * _QTY
        )
        down_leg = SideLeg(
            side="Down", entry_price=_DOWN_PRICE, quantity=_QTY, cost_basis=_DOWN_PRICE * _QTY
        )
        trader._positions["cond_a"] = PairedPosition(
            opportunity=opp,
            state=PositionState.PAIRED,
            up_leg=up_leg,
            down_leg=down_leg,
            entry_time=_PAST_TS,
        )

        with (
            patch.object(trader, "_resolve_outcome", return_value="Up"),
            patch("trading_tools.apps.spread_capture.spread_trader.time") as mock_time,
        ):
            mock_time.time.return_value = _NOW
            await trader._settle_expired_positions()

        result = trader._results[0]
        expected_pnl = _QTY * Decimal(1) - (_UP_PRICE * _QTY + _DOWN_PRICE * _QTY)
        assert result.pnl == expected_pnl
