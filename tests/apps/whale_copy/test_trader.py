"""Tests for the whale copy trader."""

from __future__ import annotations

import time
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

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
from trading_tools.clients.binance.exceptions import BinanceError
from trading_tools.core.models import ONE, ZERO, Candle, Interval

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
    async def test_skip_no_whale_activity(self) -> None:
        """Skip fills when no whale trades exist in the window."""
        config = WhaleCopyConfig(max_fill_age_pct=Decimal("0.95"))
        signal = AsyncMock(spec=WhaleSignalClient)
        signal.get_volumes = AsyncMock(return_value=(ZERO, ZERO))

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

        assert pos.up_leg.quantity == ZERO
        assert pos.whale_side is None

    @pytest.mark.asyncio
    async def test_skip_past_fill_cutoff(self) -> None:
        """Skip fills when past the fill age cutoff."""
        config = WhaleCopyConfig(max_fill_age_pct=Decimal("0.50"))
        signal = AsyncMock(spec=WhaleSignalClient)
        signal.get_volumes = AsyncMock(return_value=(Decimal(50), Decimal(10)))

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


# ---------------------------------------------------------------------------
# Constants for assertions (avoid PLR2004 magic values)
# ---------------------------------------------------------------------------
_EXPECTED_FILL_QTY = Decimal(5)
_ASK_PRICE = Decimal("0.45")
_ASK_DEPTH = Decimal(100)
_HIGH_CONVICTION = Decimal("3.0")
_BASE_CAPITAL = Decimal(1000)
_BUDGET_100 = Decimal(100)
_PNL_POSITIVE = Decimal("5.50")
_PNL_NEGATIVE = Decimal("-4.50")
_SLIPPAGE_FACTOR = ONE + Decimal("0.005")
_EXPECTED_PAPER_PRICE = _ASK_PRICE * _SLIPPAGE_FACTOR


def _make_trader(
    *,
    config: WhaleCopyConfig | None = None,
    signal: WhaleSignalClient | None = None,
) -> WhaleCopyTrader:
    """Create a paper-mode trader with sensible defaults."""
    if config is None:
        config = WhaleCopyConfig(fee_rate=ZERO)
    if signal is None:
        signal = WhaleSignalClient(whale_addresses=())
    return WhaleCopyTrader(config=config, signal_client=signal)


def _make_ask_level(price: Decimal = _ASK_PRICE, size: Decimal = _ASK_DEPTH) -> SimpleNamespace:
    """Create a mock order-book ask level with price and size attributes."""
    return SimpleNamespace(price=price, size=size)


def _make_candle(*, open_price: Decimal, close_price: Decimal, ts: int = 0) -> Candle:
    """Create a Candle with the given open/close for outcome resolution tests."""
    high = max(open_price, close_price)
    low = min(open_price, close_price)
    return Candle(
        symbol="BTC-USD",
        timestamp=ts,
        open=open_price,
        high=high,
        low=low,
        close=close_price,
        volume=Decimal(100),
        interval=Interval.M1,
    )


class TestPostInit:
    """Test __post_init__ service initialization."""

    def test_paper_mode_no_services(self) -> None:
        """Leave live services as None when live=False."""
        trader = _make_trader()
        assert trader._redeemer is None
        assert trader._executor is None
        assert trader._balance_manager is None

    def test_live_mode_initialises_services(self) -> None:
        """Initialize redeemer, executor, and balance manager in live mode."""
        mock_client = MagicMock()
        config = WhaleCopyConfig(fee_rate=ZERO)
        signal = WhaleSignalClient(whale_addresses=())
        trader = WhaleCopyTrader(
            config=config,
            signal_client=signal,
            live=True,
            client=mock_client,
        )
        assert trader._redeemer is not None
        assert trader._executor is not None
        assert trader._balance_manager is not None


class TestProperties:
    """Test public read-only properties."""

    def test_positions_returns_copy(self) -> None:
        """Return a copy of the positions dict, not the internal reference."""
        trader = _make_trader()
        pos = _make_position()
        trader._positions["cond_abc"] = pos

        result = trader.positions
        assert result == {"cond_abc": pos}
        # Mutating the copy must not affect the internal state
        result.pop("cond_abc")
        assert "cond_abc" in trader._positions

    def test_results_returns_copy(self) -> None:
        """Return a copy of the results list, not the internal reference."""
        trader = _make_trader()
        opp = _make_opportunity()
        result = SpreadResult(
            opportunity=opp,
            state=PositionState.SETTLED,
            up_entry=Decimal("0.50"),
            up_qty=Decimal(10),
            down_entry=None,
            down_qty=None,
            total_cost_basis=Decimal("4.50"),
            entry_time=0,
            exit_time=300,
            pnl=_PNL_POSITIVE,
        )
        trader._results.append(result)

        copy = trader.results
        assert len(copy) == 1
        copy.clear()
        assert len(trader._results) == 1

    def test_poll_count(self) -> None:
        """Return the current poll count."""
        trader = _make_trader()
        assert trader.poll_count == 0
        trader._poll_count = 42
        _expected_polls = 42
        assert trader.poll_count == _expected_polls


class TestSetRepoAndStop:
    """Test set_repo and stop helper methods."""

    def test_set_repo_attaches_repository(self) -> None:
        """Attach a repository instance to the trader."""
        trader = _make_trader()
        mock_repo = MagicMock()
        trader.set_repo(mock_repo)
        assert trader._repo is mock_repo

    def test_stop_requests_shutdown(self) -> None:
        """Signal the shutdown flag via stop()."""
        trader = _make_trader()
        assert not trader._shutdown.should_stop
        trader.stop()
        assert trader._shutdown.should_stop


class TestGetCapital:
    """Test paper capital computation."""

    def test_base_capital_no_results(self) -> None:
        """Return configured capital when no trades have completed."""
        config = WhaleCopyConfig(capital=_BASE_CAPITAL, fee_rate=ZERO)
        trader = _make_trader(config=config)
        assert trader._get_capital() == _BASE_CAPITAL

    def test_compound_profits_adds_pnl(self) -> None:
        """Add realised P&L to base capital when compound_profits is True."""
        config = WhaleCopyConfig(
            capital=_BASE_CAPITAL,
            compound_profits=True,
            fee_rate=ZERO,
        )
        trader = _make_trader(config=config)

        opp = _make_opportunity()
        result = SpreadResult(
            opportunity=opp,
            state=PositionState.SETTLED,
            up_entry=Decimal("0.45"),
            up_qty=Decimal(10),
            down_entry=None,
            down_qty=None,
            total_cost_basis=Decimal("4.50"),
            entry_time=0,
            exit_time=300,
            pnl=Decimal(50),
        )
        trader._results.append(result)

        expected = _BASE_CAPITAL + Decimal(50)
        assert trader._get_capital() == expected

    def test_no_compound_ignores_pnl(self) -> None:
        """Ignore realised P&L when compound_profits is False."""
        config = WhaleCopyConfig(
            capital=_BASE_CAPITAL,
            compound_profits=False,
            fee_rate=ZERO,
        )
        trader = _make_trader(config=config)

        opp = _make_opportunity()
        result = SpreadResult(
            opportunity=opp,
            state=PositionState.SETTLED,
            up_entry=Decimal("0.45"),
            up_qty=Decimal(10),
            down_entry=None,
            down_qty=None,
            total_cost_basis=Decimal("4.50"),
            entry_time=0,
            exit_time=300,
            pnl=Decimal(50),
        )
        trader._results.append(result)

        assert trader._get_capital() == _BASE_CAPITAL

    def test_committed_capital_subtracted(self) -> None:
        """Subtract open position cost basis from available capital."""
        config = WhaleCopyConfig(capital=_BASE_CAPITAL, fee_rate=ZERO)
        trader = _make_trader(config=config)

        pos = _make_position(up_qty=Decimal(10))  # cost_basis = 4.50
        trader._positions["cond_abc"] = pos

        expected = _BASE_CAPITAL - Decimal("4.50")
        assert trader._get_capital() == expected


class TestTotalCapital:
    """Test total capital calculation."""

    def test_total_includes_committed(self) -> None:
        """Return base capital (without subtracting committed) for total."""
        config = WhaleCopyConfig(capital=_BASE_CAPITAL, fee_rate=ZERO)
        trader = _make_trader(config=config)

        pos = _make_position(up_qty=Decimal(10))
        trader._positions["cond_abc"] = pos

        assert trader._total_capital() == _BASE_CAPITAL

    def test_total_with_compound_profits(self) -> None:
        """Include realised P&L in total capital when compounding."""
        config = WhaleCopyConfig(
            capital=_BASE_CAPITAL,
            compound_profits=True,
            fee_rate=ZERO,
        )
        trader = _make_trader(config=config)

        opp = _make_opportunity()
        result = SpreadResult(
            opportunity=opp,
            state=PositionState.SETTLED,
            up_entry=Decimal("0.45"),
            up_qty=Decimal(10),
            down_entry=None,
            down_qty=None,
            total_cost_basis=Decimal("4.50"),
            entry_time=0,
            exit_time=300,
            pnl=Decimal(50),
        )
        trader._results.append(result)

        expected = _BASE_CAPITAL + Decimal(50)
        assert trader._total_capital() == expected


class TestFillPositionsPositivePath:
    """Test the fill happy path: whale signal found, paper fill executed."""

    @pytest.mark.asyncio
    async def test_fill_with_valid_signal(self) -> None:
        """Execute paper fill when whale signal meets conviction threshold."""
        config = WhaleCopyConfig(
            min_whale_conviction=Decimal("1.5"),
            max_fill_age_pct=Decimal("0.95"),
            max_price=Decimal("0.60"),
            fill_size_tokens=_EXPECTED_FILL_QTY,
            max_book_pct=Decimal("0.50"),
            paper_slippage_pct=Decimal("0.005"),
            fee_rate=ZERO,
        )
        signal = AsyncMock(spec=WhaleSignalClient)
        signal.get_volumes = AsyncMock(return_value=(Decimal(100), Decimal(10)))

        mock_client = AsyncMock()
        book = SimpleNamespace(asks=[_make_ask_level()])
        mock_client.get_order_book = AsyncMock(return_value=book)

        trader = WhaleCopyTrader(
            config=config,
            signal_client=signal,
            client=mock_client,
        )

        now = int(time.time())
        pos = _make_position(
            whale_side=None,
            up_qty=ZERO,
            window_start=now - 30,
            window_end=now + 270,
        )
        trader._positions["cond_abc"] = pos

        await trader._fill_positions()

        assert pos.whale_side == "Up"
        assert pos.up_leg.quantity == _EXPECTED_FILL_QTY
        expected_cost = _EXPECTED_PAPER_PRICE * _EXPECTED_FILL_QTY
        assert pos.up_leg.cost_basis == expected_cost

    @pytest.mark.asyncio
    async def test_whale_flip_logged(self) -> None:
        """Log a whale flip when direction changes from previous poll."""
        config = WhaleCopyConfig(
            min_whale_conviction=Decimal("1.5"),
            max_fill_age_pct=Decimal("0.95"),
            max_price=Decimal("0.60"),
            fill_size_tokens=_EXPECTED_FILL_QTY,
            max_book_pct=Decimal("0.50"),
            fee_rate=ZERO,
        )
        signal = AsyncMock(spec=WhaleSignalClient)
        # Whale flips from Up to Down
        signal.get_volumes = AsyncMock(return_value=(Decimal(10), Decimal(100)))

        mock_client = AsyncMock()
        book = SimpleNamespace(asks=[_make_ask_level()])
        mock_client.get_order_book = AsyncMock(return_value=book)

        trader = WhaleCopyTrader(
            config=config,
            signal_client=signal,
            client=mock_client,
        )

        now = int(time.time())
        pos = _make_position(
            whale_side="Up",  # previously Up
            up_qty=_EXPECTED_FILL_QTY,
            window_start=now - 30,
            window_end=now + 270,
        )
        # Budget large enough for more fills
        pos.budget = Decimal(500)
        trader._positions["cond_abc"] = pos

        await trader._fill_positions()

        # Whale side should have flipped to Down
        assert pos.whale_side == "Down"
        # Down leg should have gotten a fill
        assert pos.down_leg.quantity == _EXPECTED_FILL_QTY

    @pytest.mark.asyncio
    async def test_skip_budget_exhausted(self) -> None:
        """Skip fills when cost basis meets or exceeds position budget."""
        config = WhaleCopyConfig(
            min_whale_conviction=Decimal("1.0"),
            max_fill_age_pct=Decimal("0.95"),
            fee_rate=ZERO,
        )
        signal = AsyncMock(spec=WhaleSignalClient)
        signal.get_volumes = AsyncMock(return_value=(Decimal(100), Decimal(10)))

        trader = WhaleCopyTrader(config=config, signal_client=signal)

        now = int(time.time())
        pos = _make_position(
            whale_side="Up",
            up_qty=Decimal(10),
            window_start=now - 30,
            window_end=now + 270,
        )
        # Set budget equal to cost basis — should skip
        pos.budget = pos.total_cost_basis
        trader._positions["cond_abc"] = pos

        await trader._fill_positions()

        # get_direction should not be called since budget check is before it
        signal.get_volumes.assert_not_called()

    @pytest.mark.asyncio
    async def test_skip_no_whale_activity(self) -> None:
        """Skip fills when whale returns no direction."""
        config = WhaleCopyConfig(
            min_whale_conviction=Decimal("1.0"),
            max_fill_age_pct=Decimal("0.95"),
            fee_rate=ZERO,
        )
        signal = AsyncMock(spec=WhaleSignalClient)
        signal.get_volumes = AsyncMock(return_value=(ZERO, ZERO))

        trader = WhaleCopyTrader(config=config, signal_client=signal)

        now = int(time.time())
        pos = _make_position(
            whale_side=None,
            up_qty=ZERO,
            window_start=now - 30,
            window_end=now + 270,
        )
        trader._positions["cond_abc"] = pos

        await trader._fill_positions()

        assert pos.whale_side is None
        assert pos.up_leg.quantity == ZERO


class TestExecuteFill:
    """Test the _execute_fill method for paper fills."""

    @pytest.mark.asyncio
    async def test_paper_fill_with_slippage(self) -> None:
        """Apply slippage to paper fill price."""
        config = WhaleCopyConfig(
            fill_size_tokens=_EXPECTED_FILL_QTY,
            max_price=Decimal("0.60"),
            max_book_pct=Decimal("0.50"),
            paper_slippage_pct=Decimal("0.005"),
            fee_rate=ZERO,
        )
        mock_client = AsyncMock()
        book = SimpleNamespace(asks=[_make_ask_level()])
        mock_client.get_order_book = AsyncMock(return_value=book)

        trader = WhaleCopyTrader(
            config=config,
            signal_client=WhaleSignalClient(whale_addresses=()),
            client=mock_client,
        )

        pos = _make_position(whale_side="Up", up_qty=ZERO)
        leg = pos.up_leg

        await trader._execute_fill(pos, leg, "tok_up", "cond_abc")

        assert leg.quantity == _EXPECTED_FILL_QTY
        assert leg.entry_price == _EXPECTED_PAPER_PRICE.quantize(Decimal("0.0001"))

    @pytest.mark.asyncio
    async def test_skip_when_ask_above_max_price(self) -> None:
        """Skip fill when best ask exceeds max_price."""
        config = WhaleCopyConfig(
            max_price=Decimal("0.40"),
            fee_rate=ZERO,
        )
        mock_client = AsyncMock()
        book = SimpleNamespace(asks=[_make_ask_level(price=Decimal("0.50"))])
        mock_client.get_order_book = AsyncMock(return_value=book)

        trader = WhaleCopyTrader(
            config=config,
            signal_client=WhaleSignalClient(whale_addresses=()),
            client=mock_client,
        )

        pos = _make_position(whale_side="Up", up_qty=ZERO)
        leg = pos.up_leg

        await trader._execute_fill(pos, leg, "tok_up", "cond_abc")

        assert leg.quantity == ZERO

    @pytest.mark.asyncio
    async def test_skip_when_depth_too_thin(self) -> None:
        """Skip fill when order book depth is too thin for minimum qty."""
        config = WhaleCopyConfig(
            fill_size_tokens=_EXPECTED_FILL_QTY,
            max_price=Decimal("0.60"),
            max_book_pct=Decimal("0.10"),  # 10% of 10 = 1 token < min 5
            fee_rate=ZERO,
        )
        mock_client = AsyncMock()
        thin_depth = Decimal(10)
        book = SimpleNamespace(asks=[_make_ask_level(size=thin_depth)])
        mock_client.get_order_book = AsyncMock(return_value=book)

        trader = WhaleCopyTrader(
            config=config,
            signal_client=WhaleSignalClient(whale_addresses=()),
            client=mock_client,
        )

        pos = _make_position(whale_side="Up", up_qty=ZERO)
        leg = pos.up_leg

        await trader._execute_fill(pos, leg, "tok_up", "cond_abc")

        assert leg.quantity == ZERO

    @pytest.mark.asyncio
    async def test_budget_constraint_reduces_qty(self) -> None:
        """Reduce fill quantity when remaining budget is tight."""
        config = WhaleCopyConfig(
            fill_size_tokens=Decimal(20),
            max_price=Decimal("0.60"),
            max_book_pct=Decimal("0.50"),
            paper_slippage_pct=ZERO,
            fee_rate=ZERO,
        )
        mock_client = AsyncMock()
        book = SimpleNamespace(asks=[_make_ask_level(size=Decimal(200))])
        mock_client.get_order_book = AsyncMock(return_value=book)

        trader = WhaleCopyTrader(
            config=config,
            signal_client=WhaleSignalClient(whale_addresses=()),
            client=mock_client,
        )

        # Position with tight remaining budget: budget=5, cost_basis=0
        # remaining = 5, fill_cost = 0.45 * 20 = 9 > 5
        # adjusted qty = 5 / 0.45 = 11.11 → quantize to 11.11
        pos = _make_position(whale_side="Up", up_qty=ZERO)
        pos.budget = Decimal(5)
        leg = pos.up_leg

        await trader._execute_fill(pos, leg, "tok_up", "cond_abc")

        # Fill should have happened with a reduced quantity
        assert leg.quantity > ZERO
        assert leg.quantity * _ASK_PRICE <= Decimal(5)

    @pytest.mark.asyncio
    async def test_no_client_returns_early(self) -> None:
        """Return immediately when no client is set."""
        trader = _make_trader()
        trader.client = None

        pos = _make_position(whale_side="Up", up_qty=ZERO)
        leg = pos.up_leg

        await trader._execute_fill(pos, leg, "tok_up", "cond_abc")

        assert leg.quantity == ZERO

    @pytest.mark.asyncio
    async def test_empty_asks_returns_early(self) -> None:
        """Return immediately when order book has no asks."""
        mock_client = AsyncMock()
        book = SimpleNamespace(asks=[])
        mock_client.get_order_book = AsyncMock(return_value=book)

        config = WhaleCopyConfig(fee_rate=ZERO)
        trader = WhaleCopyTrader(
            config=config,
            signal_client=WhaleSignalClient(whale_addresses=()),
            client=mock_client,
        )

        pos = _make_position(whale_side="Up", up_qty=ZERO)
        leg = pos.up_leg

        await trader._execute_fill(pos, leg, "tok_up", "cond_abc")

        assert leg.quantity == ZERO

    @pytest.mark.asyncio
    async def test_order_book_fetch_error(self) -> None:
        """Handle order book fetch failure gracefully."""
        mock_client = AsyncMock()
        mock_client.get_order_book = AsyncMock(side_effect=Exception("network"))

        config = WhaleCopyConfig(fee_rate=ZERO)
        trader = WhaleCopyTrader(
            config=config,
            signal_client=WhaleSignalClient(whale_addresses=()),
            client=mock_client,
        )

        pos = _make_position(whale_side="Up", up_qty=ZERO)
        leg = pos.up_leg

        await trader._execute_fill(pos, leg, "tok_up", "cond_abc")

        assert leg.quantity == ZERO


class TestSettleExpiredPositions:
    """Test settlement of expired positions."""

    @pytest.mark.asyncio
    async def test_settle_expired_with_known_outcome(self) -> None:
        """Settle an expired position and produce a SpreadResult."""
        config = WhaleCopyConfig(fee_rate=ZERO, compound_profits=False)
        signal = WhaleSignalClient(whale_addresses=())
        trader = WhaleCopyTrader(config=config, signal_client=signal)
        trader._session_start_capital = _BASE_CAPITAL
        trader._high_water_mark = _BASE_CAPITAL

        now = int(time.time())
        pos = _make_position(
            whale_side="Up",
            up_qty=Decimal(10),
            window_start=now - 600,
            window_end=now - 1,  # already expired
        )
        trader._positions["cond_abc"] = pos

        # Mock _resolve_outcome to return "Up" (whale was correct)
        trader._resolve_outcome = AsyncMock(return_value="Up")  # type: ignore[method-assign]

        await trader._settle_expired_positions()

        assert "cond_abc" not in trader._positions
        assert len(trader._results) == 1

        result = trader._results[0]
        assert result.state == PositionState.SETTLED
        assert result.winning_side == "Up"
        assert result.pnl == _PNL_POSITIVE
        assert result.outcome_known is True

    @pytest.mark.asyncio
    async def test_settle_losing_position_records_loss(self) -> None:
        """Record a loss and increment consecutive loss counter on settlement."""
        config = WhaleCopyConfig(fee_rate=ZERO, circuit_breaker_losses=5)
        signal = WhaleSignalClient(whale_addresses=())
        trader = WhaleCopyTrader(config=config, signal_client=signal)
        trader._session_start_capital = _BASE_CAPITAL
        trader._high_water_mark = _BASE_CAPITAL

        now = int(time.time())
        pos = _make_position(
            whale_side="Up",
            up_qty=Decimal(10),
            window_start=now - 600,
            window_end=now - 1,
        )
        trader._positions["cond_abc"] = pos

        trader._resolve_outcome = AsyncMock(return_value="Down")  # type: ignore[method-assign]

        await trader._settle_expired_positions()

        assert len(trader._results) == 1
        assert trader._results[0].pnl == _PNL_NEGATIVE
        assert trader._consecutive_losses == 1

    @pytest.mark.asyncio
    async def test_settle_unknown_outcome(self) -> None:
        """Handle unknown outcome (None) during settlement."""
        config = WhaleCopyConfig(fee_rate=ZERO)
        signal = WhaleSignalClient(whale_addresses=())
        trader = WhaleCopyTrader(config=config, signal_client=signal)
        trader._session_start_capital = _BASE_CAPITAL
        trader._high_water_mark = _BASE_CAPITAL

        now = int(time.time())
        pos = _make_position(
            whale_side="Up",
            up_qty=Decimal(10),
            window_start=now - 600,
            window_end=now - 1,
        )
        trader._positions["cond_abc"] = pos

        trader._resolve_outcome = AsyncMock(return_value=None)  # type: ignore[method-assign]

        await trader._settle_expired_positions()

        result = trader._results[0]
        assert result.outcome_known is False
        assert result.winning_side is None

    @pytest.mark.asyncio
    async def test_winning_position_resets_consecutive_losses(self) -> None:
        """Reset consecutive loss counter after a winning trade."""
        config = WhaleCopyConfig(fee_rate=ZERO)
        signal = WhaleSignalClient(whale_addresses=())
        trader = WhaleCopyTrader(config=config, signal_client=signal)
        trader._session_start_capital = _BASE_CAPITAL
        trader._high_water_mark = _BASE_CAPITAL
        trader._consecutive_losses = 3

        now = int(time.time())
        pos = _make_position(
            whale_side="Up",
            up_qty=Decimal(10),
            window_start=now - 600,
            window_end=now - 1,
        )
        trader._positions["cond_abc"] = pos

        trader._resolve_outcome = AsyncMock(return_value="Up")  # type: ignore[method-assign]

        await trader._settle_expired_positions()

        assert trader._consecutive_losses == 0

    @pytest.mark.asyncio
    async def test_winning_updates_high_water_mark(self) -> None:
        """Update the high water mark after a winning trade."""
        config = WhaleCopyConfig(fee_rate=ZERO, compound_profits=True)
        signal = WhaleSignalClient(whale_addresses=())
        trader = WhaleCopyTrader(config=config, signal_client=signal)
        trader._session_start_capital = _BASE_CAPITAL
        trader._high_water_mark = _BASE_CAPITAL

        now = int(time.time())
        pos = _make_position(
            whale_side="Up",
            up_qty=Decimal(10),
            window_start=now - 600,
            window_end=now - 1,
        )
        trader._positions["cond_abc"] = pos

        trader._resolve_outcome = AsyncMock(return_value="Up")  # type: ignore[method-assign]

        await trader._settle_expired_positions()

        assert trader._high_water_mark > _BASE_CAPITAL


class TestResolveOutcome:
    """Test Binance candle-based outcome resolution."""

    @pytest.mark.asyncio
    async def test_price_went_up(self) -> None:
        """Return 'Up' when close price exceeds open price."""
        trader = _make_trader()
        mock_binance = AsyncMock()
        trader._binance = mock_binance

        candles = [
            _make_candle(open_price=Decimal(100), close_price=Decimal(100)),
            _make_candle(open_price=Decimal(100), close_price=Decimal(105)),
        ]

        with patch(
            "trading_tools.apps.whale_copy.trader.BinanceCandleProvider"
        ) as mock_provider_cls:
            mock_provider = AsyncMock()
            mock_provider.get_candles = AsyncMock(return_value=candles)
            mock_provider_cls.return_value = mock_provider

            pos = _make_position()
            result = await trader._resolve_outcome(pos)

        assert result == "Up"

    @pytest.mark.asyncio
    async def test_price_went_down(self) -> None:
        """Return 'Down' when close price is below open price."""
        trader = _make_trader()
        mock_binance = AsyncMock()
        trader._binance = mock_binance

        candles = [
            _make_candle(open_price=Decimal(100), close_price=Decimal(100)),
            _make_candle(open_price=Decimal(100), close_price=Decimal(95)),
        ]

        with patch(
            "trading_tools.apps.whale_copy.trader.BinanceCandleProvider"
        ) as mock_provider_cls:
            mock_provider = AsyncMock()
            mock_provider.get_candles = AsyncMock(return_value=candles)
            mock_provider_cls.return_value = mock_provider

            pos = _make_position()
            result = await trader._resolve_outcome(pos)

        assert result == "Down"

    @pytest.mark.asyncio
    async def test_flat_price_returns_none(self) -> None:
        """Return None when close equals open (no movement)."""
        trader = _make_trader()
        mock_binance = AsyncMock()
        trader._binance = mock_binance

        candles = [
            _make_candle(open_price=Decimal(100), close_price=Decimal(100)),
        ]

        with patch(
            "trading_tools.apps.whale_copy.trader.BinanceCandleProvider"
        ) as mock_provider_cls:
            mock_provider = AsyncMock()
            mock_provider.get_candles = AsyncMock(return_value=candles)
            mock_provider_cls.return_value = mock_provider

            pos = _make_position()
            result = await trader._resolve_outcome(pos)

        assert result is None

    @pytest.mark.asyncio
    async def test_no_candles_returns_none(self) -> None:
        """Return None when Binance returns no candle data."""
        trader = _make_trader()
        mock_binance = AsyncMock()
        trader._binance = mock_binance

        with patch(
            "trading_tools.apps.whale_copy.trader.BinanceCandleProvider"
        ) as mock_provider_cls:
            mock_provider = AsyncMock()
            mock_provider.get_candles = AsyncMock(return_value=[])
            mock_provider_cls.return_value = mock_provider

            pos = _make_position()
            result = await trader._resolve_outcome(pos)

        assert result is None

    @pytest.mark.asyncio
    async def test_binance_error_returns_none(self) -> None:
        """Return None when Binance API raises an error."""
        trader = _make_trader()
        mock_binance = AsyncMock()
        trader._binance = mock_binance

        with patch(
            "trading_tools.apps.whale_copy.trader.BinanceCandleProvider"
        ) as mock_provider_cls:
            mock_provider = AsyncMock()
            mock_provider.get_candles = AsyncMock(side_effect=BinanceError("fail"))
            mock_provider_cls.return_value = mock_provider

            pos = _make_position()
            result = await trader._resolve_outcome(pos)

        assert result is None

    @pytest.mark.asyncio
    async def test_no_binance_client_returns_none(self) -> None:
        """Return None when Binance client is not initialised."""
        trader = _make_trader()
        trader._binance = None

        pos = _make_position()
        result = await trader._resolve_outcome(pos)

        assert result is None


class TestPollCycle:
    """Test the _poll_cycle method."""

    @pytest.mark.asyncio
    async def test_poll_cycle_increments_count(self) -> None:
        """Increment poll count on each cycle."""
        config = WhaleCopyConfig(fee_rate=ZERO, max_open_positions=0)
        signal = AsyncMock(spec=WhaleSignalClient)
        trader = WhaleCopyTrader(config=config, signal_client=signal)

        mock_scanner = AsyncMock()
        mock_scanner.scan_per_side = AsyncMock(return_value=[])
        trader._scanner = mock_scanner

        initial_count = trader._poll_count
        await trader._poll_cycle()

        expected_count = initial_count + 1
        assert trader._poll_count == expected_count

    @pytest.mark.asyncio
    async def test_poll_cycle_opens_new_position(self) -> None:
        """Open a new position when opportunities are discovered."""
        config = WhaleCopyConfig(
            fee_rate=ZERO,
            capital=_BASE_CAPITAL,
            max_open_positions=5,
            max_position_pct=Decimal("0.10"),
            max_price=Decimal("0.60"),
            fill_size_tokens=_EXPECTED_FILL_QTY,
        )
        signal = AsyncMock(spec=WhaleSignalClient)
        signal.get_volumes = AsyncMock(return_value=(ZERO, ZERO))

        trader = WhaleCopyTrader(config=config, signal_client=signal)
        trader._session_start_capital = _BASE_CAPITAL

        opp = _make_opportunity(condition_id="new_cond")
        mock_scanner = AsyncMock()
        mock_scanner.scan_per_side = AsyncMock(return_value=[opp])
        trader._scanner = mock_scanner

        await trader._poll_cycle()

        assert "new_cond" in trader._positions
        pos = trader._positions["new_cond"]
        assert pos.state == PositionState.ACCUMULATING

    @pytest.mark.asyncio
    async def test_poll_cycle_respects_max_open_positions(self) -> None:
        """Do not open positions beyond max_open_positions limit."""
        now = int(time.time())
        config = WhaleCopyConfig(
            fee_rate=ZERO,
            capital=_BASE_CAPITAL,
            max_open_positions=1,
            max_position_pct=Decimal("0.10"),
            max_price=Decimal("0.60"),
            fill_size_tokens=_EXPECTED_FILL_QTY,
        )
        signal = AsyncMock(spec=WhaleSignalClient)
        signal.get_volumes = AsyncMock(return_value=(ZERO, ZERO))

        trader = WhaleCopyTrader(config=config, signal_client=signal)
        trader._session_start_capital = _BASE_CAPITAL

        # Already have one open position with a future window_end
        existing = _make_position(
            whale_side="Up",
            window_start=now - 30,
            window_end=now + 270,
        )
        trader._positions["existing"] = existing

        opp = _make_opportunity(condition_id="new_cond")
        mock_scanner = AsyncMock()
        mock_scanner.scan_per_side = AsyncMock(return_value=[opp])
        trader._scanner = mock_scanner

        await trader._poll_cycle()

        assert "new_cond" not in trader._positions

    @pytest.mark.asyncio
    async def test_poll_cycle_no_scanner_raises(self) -> None:
        """Raise RuntimeError when scanner is not initialised."""
        trader = _make_trader()
        trader._scanner = None

        with pytest.raises(RuntimeError, match="MarketScanner not initialised"):
            await trader._poll_cycle()


class TestPersistResult:
    """Test result persistence."""

    @pytest.mark.asyncio
    async def test_no_repo_returns_silently(self) -> None:
        """Return without error when no repository is attached."""
        trader = _make_trader()
        trader._repo = None

        opp = _make_opportunity()
        result = SpreadResult(
            opportunity=opp,
            state=PositionState.SETTLED,
            up_entry=Decimal("0.45"),
            up_qty=Decimal(10),
            down_entry=None,
            down_qty=None,
            total_cost_basis=Decimal("4.50"),
            entry_time=0,
            exit_time=300,
            pnl=_PNL_POSITIVE,
        )

        # Should not raise
        await trader._persist_result(result)

    @pytest.mark.asyncio
    async def test_with_repo_calls_save(self) -> None:
        """Call save_result on the attached repository."""
        trader = _make_trader()
        mock_repo = AsyncMock()
        trader._repo = mock_repo

        opp = _make_opportunity()
        result = SpreadResult(
            opportunity=opp,
            state=PositionState.SETTLED,
            up_entry=Decimal("0.45"),
            up_qty=Decimal(10),
            down_entry=None,
            down_qty=None,
            total_cost_basis=Decimal("4.50"),
            entry_time=0,
            exit_time=300,
            pnl=_PNL_POSITIVE,
        )

        await trader._persist_result(result)

        mock_repo.save_result.assert_called_once()

    @pytest.mark.asyncio
    async def test_repo_error_handled_gracefully(self) -> None:
        """Handle repository save errors without raising."""
        trader = _make_trader()
        mock_repo = AsyncMock()
        mock_repo.save_result = AsyncMock(side_effect=OSError("disk full"))
        trader._repo = mock_repo

        opp = _make_opportunity()
        result = SpreadResult(
            opportunity=opp,
            state=PositionState.SETTLED,
            up_entry=Decimal("0.45"),
            up_qty=Decimal(10),
            down_entry=None,
            down_qty=None,
            total_cost_basis=Decimal("4.50"),
            entry_time=0,
            exit_time=300,
            pnl=_PNL_POSITIVE,
        )

        # Should not raise
        await trader._persist_result(result)


class TestLogMethods:
    """Test logging methods for coverage (no assertions on log content)."""

    def test_log_heartbeat(self) -> None:
        """Call _log_heartbeat without error."""
        trader = _make_trader()
        trader._poll_count = 1

        # Should not raise
        trader._log_heartbeat()

    def test_log_heartbeat_with_scanner(self) -> None:
        """Call _log_heartbeat with a scanner attached."""
        trader = _make_trader()
        trader._poll_count = 1
        mock_scanner = MagicMock()
        mock_scanner.known_market_count = 5
        trader._scanner = mock_scanner

        trader._log_heartbeat()

    def test_log_periodic_summary_no_results(self) -> None:
        """Call _log_periodic_summary with no results."""
        trader = _make_trader()
        trader._log_periodic_summary()

    def test_log_periodic_summary_with_results(self) -> None:
        """Call _log_periodic_summary with results and open positions."""
        trader = _make_trader()

        opp = _make_opportunity()
        result = SpreadResult(
            opportunity=opp,
            state=PositionState.SETTLED,
            up_entry=Decimal("0.45"),
            up_qty=Decimal(10),
            down_entry=None,
            down_qty=None,
            total_cost_basis=Decimal("4.50"),
            entry_time=0,
            exit_time=300,
            pnl=_PNL_POSITIVE,
        )
        trader._results.append(result)

        pos = _make_position()
        trader._positions["cond_abc"] = pos

        trader._log_periodic_summary()

    def test_log_summary(self) -> None:
        """Call _log_summary without error."""
        trader = _make_trader()
        trader._poll_count = 10

        opp = _make_opportunity()
        result = SpreadResult(
            opportunity=opp,
            state=PositionState.SETTLED,
            up_entry=Decimal("0.45"),
            up_qty=Decimal(10),
            down_entry=None,
            down_qty=None,
            total_cost_basis=Decimal("4.50"),
            entry_time=0,
            exit_time=300,
            pnl=_PNL_POSITIVE,
        )
        trader._results.append(result)

        trader._log_summary()


class TestRunErrors:
    """Test run() error handling."""

    @pytest.mark.asyncio
    async def test_run_without_client_raises(self) -> None:
        """Raise RuntimeError when client is not provided."""
        config = WhaleCopyConfig()
        signal = WhaleSignalClient(whale_addresses=())
        trader = WhaleCopyTrader(config=config, signal_client=signal, client=None)

        with pytest.raises(RuntimeError, match="PolymarketClient is required"):
            await trader.run()


class TestLiveExecuteFill:
    """Test live order execution path in _execute_fill."""

    @pytest.mark.asyncio
    async def test_live_fill_records_order(self) -> None:
        """Place a live order and record fill on the leg."""
        config = WhaleCopyConfig(
            fill_size_tokens=Decimal(5),
            max_price=Decimal("0.60"),
            max_book_pct=Decimal("1.0"),
        )
        signal = WhaleSignalClient(whale_addresses=())

        # Mock client and executor
        mock_client = AsyncMock()
        mock_book = AsyncMock()
        mock_ask = AsyncMock()
        mock_ask.price = Decimal("0.45")
        mock_ask.size = Decimal(100)
        mock_book.asks = [mock_ask]
        mock_client.get_order_book = AsyncMock(return_value=mock_book)

        trader = WhaleCopyTrader(config=config, signal_client=signal, live=True, client=mock_client)

        # Mock executor response
        mock_resp = AsyncMock()
        mock_resp.filled = Decimal(5)
        mock_resp.price = Decimal("0.45")
        mock_resp.order_id = "order_123"
        trader._executor = AsyncMock()
        trader._executor.place_order = AsyncMock(return_value=mock_resp)

        now = int(time.time())
        pos = _make_position(
            whale_side="Up", up_qty=ZERO, window_start=now - 60, window_end=now + 240
        )
        pos.budget = Decimal(100)
        leg = pos.up_leg

        await trader._execute_fill(pos, leg, "tok_up", "cond_abc")

        assert leg.quantity == Decimal(5)
        assert "order_123" in leg.order_ids

    @pytest.mark.asyncio
    async def test_live_fill_no_response(self) -> None:
        """Handle None response from executor gracefully."""
        config = WhaleCopyConfig(
            fill_size_tokens=Decimal(5),
            max_price=Decimal("0.60"),
            max_book_pct=Decimal("1.0"),
        )
        signal = WhaleSignalClient(whale_addresses=())
        mock_client = AsyncMock()
        mock_book = AsyncMock()
        mock_ask = AsyncMock()
        mock_ask.price = Decimal("0.45")
        mock_ask.size = Decimal(100)
        mock_book.asks = [mock_ask]
        mock_client.get_order_book = AsyncMock(return_value=mock_book)

        trader = WhaleCopyTrader(config=config, signal_client=signal, live=True, client=mock_client)
        trader._executor = AsyncMock()
        trader._executor.place_order = AsyncMock(return_value=None)

        now = int(time.time())
        pos = _make_position(
            whale_side="Up", up_qty=ZERO, window_start=now - 60, window_end=now + 240
        )
        pos.budget = Decimal(100)

        await trader._execute_fill(pos, pos.up_leg, "tok_up", "cond_abc")

        assert pos.up_leg.quantity == ZERO


class TestResolveOutcomeLive:
    """Test live outcome resolution via Polymarket redeemable positions."""

    @pytest.mark.asyncio
    async def test_redeemable_position_found(self) -> None:
        """Return outcome from redeemable position when found."""
        config = WhaleCopyConfig(fee_rate=ZERO)
        signal = WhaleSignalClient(whale_addresses=())
        mock_client = AsyncMock()

        rp = AsyncMock()
        rp.condition_id = "cond_abc"
        rp.outcome = "Up"
        mock_client.get_redeemable_positions = AsyncMock(return_value=[rp])

        trader = WhaleCopyTrader(config=config, signal_client=signal, client=mock_client)

        pos = _make_position()

        result = await trader._resolve_outcome_live(pos)

        assert result == "Up"

    @pytest.mark.asyncio
    async def test_falls_back_to_binance(self) -> None:
        """Fall back to Binance resolution when no redeemable positions match."""
        config = WhaleCopyConfig(fee_rate=ZERO)
        signal = WhaleSignalClient(whale_addresses=())
        mock_client = AsyncMock()
        mock_client.get_redeemable_positions = AsyncMock(return_value=[])

        trader = WhaleCopyTrader(config=config, signal_client=signal, client=mock_client)
        trader._binance = None

        pos = _make_position()

        result = await trader._resolve_outcome_live(pos)

        # Falls back to _resolve_outcome which returns None without Binance
        assert result is None

    @pytest.mark.asyncio
    async def test_api_error_falls_back(self) -> None:
        """Fall back to Binance when Polymarket API errors."""
        config = WhaleCopyConfig(fee_rate=ZERO)
        signal = WhaleSignalClient(whale_addresses=())
        mock_client = AsyncMock()
        mock_client.get_redeemable_positions = AsyncMock(side_effect=Exception("API down"))

        trader = WhaleCopyTrader(config=config, signal_client=signal, client=mock_client)
        trader._binance = None

        pos = _make_position()

        result = await trader._resolve_outcome_live(pos)

        assert result is None


class TestCapitalWithBalanceManager:
    """Test capital methods when a balance manager is present."""

    def test_get_capital_uses_balance_manager(self) -> None:
        """Return balance from balance manager when available."""
        config = WhaleCopyConfig(capital=Decimal(1000))
        signal = WhaleSignalClient(whale_addresses=())
        trader = WhaleCopyTrader(config=config, signal_client=signal)

        trader._balance_manager = AsyncMock()
        trader._balance_manager.balance = Decimal(750)

        assert trader._get_capital() == Decimal(750)

    def test_total_capital_uses_balance_manager(self) -> None:
        """Return balance + committed from balance manager."""
        config = WhaleCopyConfig(capital=Decimal(1000))
        signal = WhaleSignalClient(whale_addresses=())
        trader = WhaleCopyTrader(config=config, signal_client=signal)

        trader._balance_manager = AsyncMock()
        trader._balance_manager.balance = Decimal(750)

        # Add a position with some cost
        now = int(time.time())
        pos = _make_position(up_qty=Decimal(10), window_start=now, window_end=now + 300)
        trader._positions["cond_abc"] = pos

        total = trader._total_capital()

        assert total == Decimal(750) + pos.total_cost_basis
