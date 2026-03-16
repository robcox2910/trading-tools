"""Tests for AccumulatingTrader trading engine."""

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from trading_tools.apps.spread_capture.accumulating_trader import AccumulatingTrader
from trading_tools.apps.spread_capture.config import SpreadCaptureConfig
from trading_tools.apps.spread_capture.models import (
    AccumulatingPosition,
    PositionState,
    SideLeg,
    SpreadOpportunity,
    SpreadResult,
)

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
_THRESHOLD = Decimal("0.48")
_MAX_VWAP = Decimal("0.98")
_MAX_IMBALANCE = Decimal("2.0")
_FILL_SIZE = Decimal(10)


def _make_config(**overrides: object) -> SpreadCaptureConfig:
    """Create a SpreadCaptureConfig with accumulate strategy defaults."""
    defaults: dict[str, object] = {
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
        "strategy": "accumulate",
        "per_side_ask_threshold": _THRESHOLD,
        "max_combined_vwap": _MAX_VWAP,
        "max_imbalance_ratio": _MAX_IMBALANCE,
        "fill_size_tokens": _FILL_SIZE,
    }
    defaults.update(overrides)
    return SpreadCaptureConfig(**defaults)  # type: ignore[arg-type]


def _make_opportunity(**overrides: object) -> SpreadOpportunity:
    """Create a SpreadOpportunity with sensible defaults."""
    defaults: dict[str, object] = {
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
        "up_ask_depth": Decimal(100),
        "down_ask_depth": Decimal(100),
    }
    defaults.update(overrides)
    return SpreadOpportunity(**defaults)  # type: ignore[arg-type]


def _make_trader(**overrides: object) -> AccumulatingTrader:
    """Create an AccumulatingTrader with mock client and sensible defaults."""
    config = overrides.pop("config", _make_config())  # type: ignore[arg-type]
    client = overrides.pop("client", AsyncMock())  # type: ignore[arg-type]
    return AccumulatingTrader(config=config, live=False, client=client, **overrides)  # type: ignore[arg-type]


def _make_accum_position(
    up_price: Decimal = Decimal(0),
    up_qty: Decimal = Decimal(0),
    down_price: Decimal = Decimal(0),
    down_qty: Decimal = Decimal(0),
    budget: Decimal = Decimal(10),
    **overrides: object,
) -> AccumulatingPosition:
    """Create an AccumulatingPosition with specified leg values."""
    opp = overrides.pop("opportunity", _make_opportunity())  # type: ignore[arg-type]
    return AccumulatingPosition(
        opportunity=opp,  # type: ignore[arg-type]
        state=PositionState.ACCUMULATING,
        up_leg=SideLeg(
            side="Up",
            entry_price=up_price,
            quantity=up_qty,
            cost_basis=up_price * up_qty,
        ),
        down_leg=SideLeg(
            side="Down",
            entry_price=down_price,
            quantity=down_qty,
            cost_basis=down_price * down_qty,
        ),
        entry_time=_NOW,
        budget=budget,
        **overrides,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
class TestFillDecisions:
    """Test per-side fill decision logic."""

    async def test_buys_up_when_ask_below_threshold(self) -> None:
        """Fill Up leg when ask is below per_side_ask_threshold."""
        trader = _make_trader()
        pos = _make_accum_position(budget=Decimal(50))
        trader._positions["cond_a"] = pos

        result = trader._should_fill_side(pos, "Up", Decimal("0.45"), _QTY)
        assert result is True

    async def test_buys_down_when_ask_below_threshold(self) -> None:
        """Fill Down leg when ask is below per_side_ask_threshold."""
        trader = _make_trader()
        pos = _make_accum_position(budget=Decimal(50))
        trader._positions["cond_a"] = pos

        result = trader._should_fill_side(pos, "Down", Decimal("0.44"), _QTY)
        assert result is True

    async def test_skips_side_when_ask_above_threshold(self) -> None:
        """No fill when ask >= per_side_ask_threshold."""
        trader = _make_trader()
        pos = _make_accum_position(budget=Decimal(50))

        result = trader._should_fill_side(pos, "Up", Decimal("0.55"), _QTY)
        assert result is False

    async def test_skips_side_when_ask_equals_threshold(self) -> None:
        """No fill when ask == per_side_ask_threshold (must be strictly below)."""
        trader = _make_trader()
        pos = _make_accum_position(budget=Decimal(50))

        result = trader._should_fill_side(pos, "Up", Decimal("0.48"), _QTY)
        assert result is False

    async def test_stops_when_combined_vwap_exceeds_max(self) -> None:
        """Block fill when hypothetical combined VWAP exceeds max_combined_vwap."""
        config = _make_config(max_combined_vwap=Decimal("0.90"))
        trader = _make_trader(config=config)
        # Position already has fills with high VWAPs
        pos = _make_accum_position(
            up_price=Decimal("0.50"),
            up_qty=Decimal(10),
            down_price=Decimal("0.45"),
            down_qty=Decimal(10),
            budget=Decimal(50),
        )

        # Adding more Up at 0.47 would make combined = ~0.485 + 0.45 = 0.935 > 0.90
        result = trader._should_fill_side(pos, "Up", Decimal("0.47"), _QTY)
        assert result is False

    async def test_imbalance_ratio_blocks_heavy_side(self) -> None:
        """Pause heavier side until other catches up."""
        config = _make_config(max_imbalance_ratio=Decimal("1.5"))
        trader = _make_trader(config=config)
        pos = _make_accum_position(
            up_price=Decimal("0.45"),
            up_qty=Decimal(20),
            down_price=Decimal("0.42"),
            down_qty=Decimal(10),
            budget=Decimal(50),
        )

        # Up has 20, Down has 10 — ratio is 2.0, adding more Up would exceed 1.5
        result = trader._should_fill_side(pos, "Up", Decimal("0.45"), _QTY)
        assert result is False

        # But filling Down is fine — it helps balance
        result = trader._should_fill_side(pos, "Down", Decimal("0.42"), _QTY)
        assert result is True

    async def test_single_side_cap_blocks_when_other_empty(self) -> None:
        """Block fills on one side when it would exceed single-side cap and other side has no fills."""
        config = _make_config(max_single_side_pct=Decimal("0.50"))
        trader = _make_trader(config=config)
        # Up side already has fills consuming 50%+ of budget, Down has zero
        pos = _make_accum_position(
            up_price=Decimal("0.45"),
            up_qty=Decimal(12),  # cost = 5.40
            budget=Decimal(10),  # cap = 5.00
        )

        # Adding more Up at 0.45 would push well over 50% cap
        result = trader._should_fill_side(pos, "Up", Decimal("0.45"), Decimal(5))
        assert result is False

    async def test_single_side_cap_allows_when_other_has_fills(self) -> None:
        """Allow fills freely once the other side also has fills."""
        config = _make_config(max_single_side_pct=Decimal("0.50"))
        trader = _make_trader(config=config)
        # Both sides have fills, balanced — no single-side cap
        pos = _make_accum_position(
            up_price=Decimal("0.45"),
            up_qty=Decimal(10),
            down_price=Decimal("0.42"),
            down_qty=Decimal(10),
            budget=Decimal(20),
        )

        result = trader._should_fill_side(pos, "Up", Decimal("0.45"), Decimal(5))
        assert result is True

    async def test_single_side_cap_allows_first_fill(self) -> None:
        """Allow the first fill on a side even when other side is empty."""
        config = _make_config(max_single_side_pct=Decimal("0.50"))
        trader = _make_trader(config=config)
        pos = _make_accum_position(budget=Decimal(20))

        result = trader._should_fill_side(pos, "Up", Decimal("0.45"), Decimal(10))
        assert result is True


@pytest.mark.asyncio
class TestMultipleFills:
    """Test VWAP tracking across multiple fills."""

    async def test_multiple_fills_update_vwap(self) -> None:
        """SideLeg.add_fill produces correct weighted average across fills."""
        leg = SideLeg(
            side="Up",
            entry_price=Decimal("0.50"),
            quantity=Decimal(10),
            cost_basis=Decimal("5.00"),
        )
        # Add a cheaper fill
        leg.add_fill(Decimal("0.40"), Decimal(10))

        # VWAP = (5.0 + 4.0) / 20 = 0.45
        assert leg.entry_price == Decimal("0.4500")
        assert leg.quantity == Decimal(20)
        assert leg.cost_basis == Decimal("9.00")


@pytest.mark.asyncio
class TestSettlement:
    """Test position settlement P&L computation."""

    async def test_settlement_paired_pnl(self) -> None:
        """Paired settlement: min(up, down) * 1.0 - total_cost - fees."""
        config = _make_config(fee_rate=_ZERO_FEE)
        trader = _make_trader(config=config)
        opp = _make_opportunity(window_end_ts=_NOW - 1)
        pos = _make_accum_position(
            up_price=Decimal("0.48"),
            up_qty=_QTY,
            down_price=Decimal("0.47"),
            down_qty=_QTY,
            budget=Decimal(50),
            opportunity=opp,
        )
        trader._positions["cond_a"] = pos

        with (
            patch.object(trader, "_resolve_outcome", return_value="Up"),
            patch("trading_tools.apps.spread_capture.accumulating_trader.time") as mock_time,
        ):
            mock_time.time.return_value = _NOW
            await trader._settle_expired_positions()

        assert len(trader._results) == 1
        result = trader._results[0]
        # P&L = winning_qty * 1.0 - total_cost = 10 * 1.0 - (4.8 + 4.7) = 0.5
        expected_pnl = _QTY * Decimal(1) - (_UP_PRICE * _QTY + _DOWN_PRICE * _QTY)
        assert result.pnl == expected_pnl
        assert result.pnl > Decimal(0)

    async def test_settlement_unpaired_winner(self) -> None:
        """Excess tokens on winning side add bonus profit."""
        config = _make_config(fee_rate=_ZERO_FEE)
        trader = _make_trader(config=config)
        opp = _make_opportunity(window_end_ts=_NOW - 1)
        # Up has more tokens than Down
        pos = _make_accum_position(
            up_price=Decimal("0.48"),
            up_qty=Decimal(15),
            down_price=Decimal("0.47"),
            down_qty=Decimal(10),
            budget=Decimal(50),
            opportunity=opp,
        )
        trader._positions["cond_a"] = pos

        with (
            patch.object(trader, "_resolve_outcome", return_value="Up"),
            patch("trading_tools.apps.spread_capture.accumulating_trader.time") as mock_time,
        ):
            mock_time.time.return_value = _NOW
            await trader._settle_expired_positions()

        result = trader._results[0]
        # P&L = winning_qty * 1.0 - total_cost
        # = 15 * 1.0 - (0.48 * 15 + 0.47 * 10) = 15 - 7.2 - 4.7 = 3.1
        expected_pnl = Decimal(15) * Decimal(1) - (
            Decimal("0.48") * Decimal(15) + Decimal("0.47") * Decimal(10)
        )
        assert result.pnl == expected_pnl

    async def test_settlement_unpaired_loser(self) -> None:
        """Excess tokens on losing side add to the loss."""
        config = _make_config(fee_rate=_ZERO_FEE)
        trader = _make_trader(config=config)
        opp = _make_opportunity(window_end_ts=_NOW - 1)
        # Up has more tokens than Down, but Down wins
        pos = _make_accum_position(
            up_price=Decimal("0.48"),
            up_qty=Decimal(15),
            down_price=Decimal("0.47"),
            down_qty=Decimal(10),
            budget=Decimal(50),
            opportunity=opp,
        )
        trader._positions["cond_a"] = pos

        with (
            patch.object(trader, "_resolve_outcome", return_value="Down"),
            patch("trading_tools.apps.spread_capture.accumulating_trader.time") as mock_time,
        ):
            mock_time.time.return_value = _NOW
            await trader._settle_expired_positions()

        result = trader._results[0]
        # P&L = winning_qty * 1.0 - total_cost
        # = 10 * 1.0 - (0.48 * 15 + 0.47 * 10) = 10 - 7.2 - 4.7 = -1.9
        expected_pnl = Decimal(10) * Decimal(1) - (
            Decimal("0.48") * Decimal(15) + Decimal("0.47") * Decimal(10)
        )
        assert result.pnl == expected_pnl
        assert result.pnl < Decimal(0)

    async def test_settlement_only_one_side_wins(self) -> None:
        """Single-side position wins when that side is the winner."""
        config = _make_config(fee_rate=_ZERO_FEE)
        trader = _make_trader(config=config)
        opp = _make_opportunity(window_end_ts=_NOW - 1)
        # Only Up side has fills
        pos = _make_accum_position(
            up_price=Decimal("0.48"),
            up_qty=_QTY,
            budget=Decimal(50),
            opportunity=opp,
        )
        trader._positions["cond_a"] = pos

        with (
            patch.object(trader, "_resolve_outcome", return_value="Up"),
            patch("trading_tools.apps.spread_capture.accumulating_trader.time") as mock_time,
        ):
            mock_time.time.return_value = _NOW
            await trader._settle_expired_positions()

        result = trader._results[0]
        # P&L = 10 * 1.0 - 4.8 = 5.2
        assert result.pnl == _QTY * Decimal(1) - _UP_PRICE * _QTY

    async def test_settlement_only_one_side_loses(self) -> None:
        """Single-side position loses when other side wins."""
        config = _make_config(fee_rate=_ZERO_FEE)
        trader = _make_trader(config=config)
        opp = _make_opportunity(window_end_ts=_NOW - 1)
        # Only Up side has fills but Down wins
        pos = _make_accum_position(
            up_price=Decimal("0.48"),
            up_qty=_QTY,
            budget=Decimal(50),
            opportunity=opp,
        )
        trader._positions["cond_a"] = pos

        with (
            patch.object(trader, "_resolve_outcome", return_value="Down"),
            patch("trading_tools.apps.spread_capture.accumulating_trader.time") as mock_time,
        ):
            mock_time.time.return_value = _NOW
            await trader._settle_expired_positions()

        result = trader._results[0]
        # P&L = 0 - 4.8 = -4.8
        assert result.pnl == Decimal(0) - _UP_PRICE * _QTY

    async def test_unknown_outcome_paired(self) -> None:
        """Unknown outcome with both sides uses paired qty as conservative estimate."""
        config = _make_config(fee_rate=_ZERO_FEE)
        trader = _make_trader(config=config)
        opp = _make_opportunity(window_end_ts=_NOW - 1)
        pos = _make_accum_position(
            up_price=Decimal("0.48"),
            up_qty=_QTY,
            down_price=Decimal("0.47"),
            down_qty=_QTY,
            budget=Decimal(50),
            opportunity=opp,
        )
        trader._positions["cond_a"] = pos

        with (
            patch.object(trader, "_resolve_outcome", return_value=None),
            patch("trading_tools.apps.spread_capture.accumulating_trader.time") as mock_time,
        ):
            mock_time.time.return_value = _NOW
            await trader._settle_expired_positions()

        result = trader._results[0]
        # Conservative: paired_qty * 1.0 - total_cost = 10 * 1.0 - 9.5 = 0.5
        assert result.pnl > Decimal(0)
        assert result.outcome_known is False


@pytest.mark.asyncio
class TestRiskManagement:
    """Test drawdown halt and circuit breaker."""

    async def test_drawdown_halt(self) -> None:
        """Drawdown halt prevents new entries when losses exceed threshold."""
        config = _make_config(max_drawdown_pct=Decimal("0.10"))
        trader = _make_trader(config=config)
        trader._session_start_capital = _CAPITAL

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

        with patch("trading_tools.apps.spread_capture.accumulating_trader.time") as mock_time:
            mock_time.time.return_value = _NOW
            trader._record_loss()
            trader._record_loss()

        assert trader._circuit_breaker_until == _NOW + 60


@pytest.mark.asyncio
class TestBudgetManagement:
    """Test per-market budget limits."""

    async def test_budget_per_market_limits_spending(self) -> None:
        """Fill qty is None when budget is exhausted."""
        trader = _make_trader()
        # Budget of $5, already spent $4.8
        pos = _make_accum_position(
            up_price=Decimal("0.48"),
            up_qty=_QTY,
            budget=Decimal("5.00"),
        )

        qty = trader._compute_fill_qty(pos, "Down", Decimal("0.47"), Decimal(100))
        # Remaining budget = 5.00 - 4.80 = 0.20
        # Max from budget = 0.20 / 0.47 ≈ 0.42 — below min 5 tokens
        assert qty is None

    async def test_fill_qty_capped_by_depth(self) -> None:
        """Fill qty is capped by max_book_pct of visible depth."""
        config = _make_config(fill_size_tokens=Decimal(100), max_book_pct=Decimal("0.20"))
        trader = _make_trader(config=config)
        pos = _make_accum_position(budget=Decimal(1000))

        # Depth is 30, max_book_pct=0.20 → max 6 tokens
        qty = trader._compute_fill_qty(pos, "Up", Decimal("0.48"), Decimal(30))
        assert qty is not None
        assert qty == Decimal("6.00")


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
        pos = _make_accum_position(
            up_price=Decimal("0.48"),
            up_qty=_QTY,
            down_price=Decimal("0.47"),
            down_qty=_QTY,
            budget=Decimal(50),
            opportunity=opp,
        )
        trader._positions["cond_a"] = pos

        with (
            patch.object(trader, "_resolve_outcome", return_value="Up"),
            patch("trading_tools.apps.spread_capture.accumulating_trader.time") as mock_time,
        ):
            mock_time.time.return_value = _NOW
            await trader._settle_expired_positions()

        mock_repo.save_result.assert_called_once()


@pytest.mark.asyncio
class TestHypotheticalVwap:
    """Test hypothetical VWAP computation."""

    async def test_hypothetical_both_sides_filled(self) -> None:
        """Compute combined VWAP including hypothetical fill on Up side."""
        trader = _make_trader()
        pos = _make_accum_position(
            up_price=Decimal("0.50"),
            up_qty=Decimal(10),
            down_price=Decimal("0.45"),
            down_qty=Decimal(10),
            budget=Decimal(50),
        )

        vwap = trader._hypothetical_combined_vwap(pos, "Up", Decimal("0.40"), Decimal(10))
        # New Up VWAP = (5.0 + 4.0) / 20 = 0.45, Down VWAP = 0.45, Combined = 0.90
        assert vwap == Decimal("0.90")

    async def test_hypothetical_zero_when_other_side_empty(self) -> None:
        """Return zero when the other side has no fills yet."""
        trader = _make_trader()
        pos = _make_accum_position(budget=Decimal(50))

        vwap = trader._hypothetical_combined_vwap(pos, "Up", Decimal("0.48"), Decimal(10))
        assert vwap == Decimal(0)

    async def test_hypothetical_first_fill_with_other_side(self) -> None:
        """Compute VWAP for first fill on one side when other has fills."""
        trader = _make_trader()
        pos = _make_accum_position(
            down_price=Decimal("0.45"),
            down_qty=Decimal(10),
            budget=Decimal(50),
        )

        vwap = trader._hypothetical_combined_vwap(pos, "Up", Decimal("0.48"), Decimal(10))
        # Up VWAP = 0.48, Down VWAP = 0.45, Combined = 0.93
        assert vwap == Decimal("0.93")
