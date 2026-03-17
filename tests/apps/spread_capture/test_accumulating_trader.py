"""Tests for AccumulatingTrader directional entry + opportunistic hedge engine."""

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
_DEFAULT_POLL = 5
_ZERO_FEE = Decimal("0.0")
_DEFAULT_FEE_EXPONENT = 2
_MAX_IMBALANCE = Decimal("3.0")
_FILL_SIZE = Decimal(5)
_HEDGE_START = Decimal("0.45")
_HEDGE_END = Decimal("0.55")
_HEDGE_START_PCT = Decimal("0.20")
_SIGNAL_DELAY = 60


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
        "max_imbalance_ratio": _MAX_IMBALANCE,
        "fill_size_tokens": _FILL_SIZE,
        "signal_delay_seconds": _SIGNAL_DELAY,
        "hedge_start_threshold": _HEDGE_START,
        "hedge_end_threshold": _HEDGE_END,
        "hedge_start_pct": _HEDGE_START_PCT,
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
    primary_side: str | None = None,
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
        primary_side=primary_side,
        **overrides,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
class TestSignalDetermination:
    """Test Binance momentum signal and primary side selection."""

    async def test_binance_up_signal(self) -> None:
        """Primary side is Up when Binance spot price rose."""
        trader = _make_trader()
        trader._binance = AsyncMock()
        pos = _make_accum_position(budget=Decimal(50))

        mock_candle_start = AsyncMock()
        mock_candle_start.open = Decimal(50000)
        mock_candle_end = AsyncMock()
        mock_candle_end.close = Decimal(50100)

        with patch(
            "trading_tools.apps.spread_capture.accumulating_trader.BinanceCandleProvider"
        ) as mock_provider_cls:
            mock_provider_cls.return_value.get_candles = AsyncMock(
                return_value=[mock_candle_start, mock_candle_end]
            )
            result = await trader._determine_primary_side(pos)

        assert result == "Up"

    async def test_binance_down_signal(self) -> None:
        """Primary side is Down when Binance spot price fell."""
        trader = _make_trader()
        trader._binance = AsyncMock()
        pos = _make_accum_position(budget=Decimal(50))

        mock_candle_start = AsyncMock()
        mock_candle_start.open = Decimal(50100)
        mock_candle_end = AsyncMock()
        mock_candle_end.close = Decimal(50000)

        with patch(
            "trading_tools.apps.spread_capture.accumulating_trader.BinanceCandleProvider"
        ) as mock_provider_cls:
            mock_provider_cls.return_value.get_candles = AsyncMock(
                return_value=[mock_candle_start, mock_candle_end]
            )
            result = await trader._determine_primary_side(pos)

        assert result == "Down"

    async def test_fallback_to_cheaper_side(self) -> None:
        """Fall back to the cheaper opportunity side when Binance unavailable."""
        trader = _make_trader()
        trader._binance = None
        pos = _make_accum_position(budget=Decimal(50))

        result = await trader._determine_primary_side(pos)
        assert result == "Down"

    async def test_fallback_when_flat(self) -> None:
        """Fall back when Binance shows no change (open == close)."""
        trader = _make_trader()
        trader._binance = AsyncMock()
        pos = _make_accum_position(budget=Decimal(50))

        mock_candle = AsyncMock()
        mock_candle.open = Decimal(50000)
        mock_candle.close = Decimal(50000)

        with patch(
            "trading_tools.apps.spread_capture.accumulating_trader.BinanceCandleProvider"
        ) as mock_provider_cls:
            mock_provider_cls.return_value.get_candles = AsyncMock(return_value=[mock_candle])
            result = await trader._determine_primary_side(pos)

        # Flat → fallback to cheaper side (Down at 0.47)
        assert result == "Down"


@pytest.mark.asyncio
class TestHedgeThreshold:
    """Test time-decaying hedge threshold computation."""

    async def test_threshold_at_hedge_start(self) -> None:
        """Threshold equals hedge_start_threshold at hedge_start_pct elapsed."""
        config = _make_config(
            hedge_start_threshold=Decimal("0.45"),
            hedge_end_threshold=Decimal("0.55"),
            hedge_start_pct=Decimal("0.20"),
            max_fill_age_pct=Decimal("0.80"),
        )
        trader = _make_trader(config=config)
        opp = _make_opportunity()

        # 20% of 300s window = 60s elapsed
        now = _WINDOW_START + 60
        threshold = trader._compute_hedge_threshold(opp, now)
        assert threshold == Decimal("0.45")

    async def test_threshold_at_fill_cutoff(self) -> None:
        """Threshold equals hedge_end_threshold at max_fill_age_pct elapsed."""
        config = _make_config(
            hedge_start_threshold=Decimal("0.45"),
            hedge_end_threshold=Decimal("0.55"),
            hedge_start_pct=Decimal("0.20"),
            max_fill_age_pct=Decimal("0.80"),
        )
        trader = _make_trader(config=config)
        opp = _make_opportunity()

        # 80% of 300s window = 240s elapsed
        now = _WINDOW_START + 240
        threshold = trader._compute_hedge_threshold(opp, now)
        assert threshold == Decimal("0.55")

    async def test_threshold_at_midpoint(self) -> None:
        """Threshold linearly interpolates at the midpoint."""
        config = _make_config(
            hedge_start_threshold=Decimal("0.45"),
            hedge_end_threshold=Decimal("0.55"),
            hedge_start_pct=Decimal("0.20"),
            max_fill_age_pct=Decimal("0.80"),
        )
        trader = _make_trader(config=config)
        opp = _make_opportunity()

        # 50% of 300s window = 150s elapsed → normalised = (0.50 - 0.20) / 0.60 = 0.50
        now = _WINDOW_START + 150
        threshold = trader._compute_hedge_threshold(opp, now)
        assert threshold == Decimal("0.50")

    async def test_threshold_before_hedge_start(self) -> None:
        """Threshold clamps to hedge_start_threshold before hedge window."""
        config = _make_config(
            hedge_start_threshold=Decimal("0.45"),
            hedge_end_threshold=Decimal("0.55"),
            hedge_start_pct=Decimal("0.20"),
            max_fill_age_pct=Decimal("0.80"),
        )
        trader = _make_trader(config=config)
        opp = _make_opportunity()

        # 10% of 300s window = 30s elapsed → before hedge_start_pct
        now = _WINDOW_START + 30
        threshold = trader._compute_hedge_threshold(opp, now)
        assert threshold == Decimal("0.45")


@pytest.mark.asyncio
class TestPrimaryFills:
    """Test that primary side fills execute without price threshold."""

    async def test_primary_fill_no_threshold(self) -> None:
        """Primary side fills execute regardless of ask price."""
        trader = _make_trader()
        pos = _make_accum_position(budget=Decimal(50), primary_side="Up")
        trader._positions["cond_a"] = pos

        # Ask at 0.60 — would fail any tight threshold but primary has none
        await trader._try_fill_primary(pos, "Up", Decimal("0.60"), Decimal(100))
        assert pos.up_leg.quantity > Decimal(0)

    async def test_primary_fill_imbalance_check(self) -> None:
        """Primary fill blocked when it would exceed imbalance ratio."""
        config = _make_config(max_imbalance_ratio=Decimal("1.5"))
        trader = _make_trader(config=config)
        pos = _make_accum_position(
            up_price=Decimal("0.50"),
            up_qty=Decimal(20),
            down_price=Decimal("0.45"),
            down_qty=Decimal(10),
            budget=Decimal(50),
            primary_side="Up",
        )

        await trader._try_fill_primary(pos, "Up", Decimal("0.50"), Decimal(100))
        # Up already at 20, Down at 10 → ratio 2.0 > 1.5 → blocked
        assert pos.up_leg.quantity == Decimal(20)


@pytest.mark.asyncio
class TestSecondaryFills:
    """Test that secondary side fills respect the hedge threshold."""

    async def test_secondary_fill_below_threshold(self) -> None:
        """Secondary fill executes when ask is below hedge threshold."""
        trader = _make_trader()
        pos = _make_accum_position(
            up_price=Decimal("0.50"),
            up_qty=Decimal(20),
            budget=Decimal(50),
            primary_side="Up",
        )

        # Ask at 0.40 < hedge threshold (0.45 at start)
        await trader._try_fill_secondary(pos, "Down", Decimal("0.40"), Decimal(100))
        assert pos.down_leg.quantity > Decimal(0)

    async def test_secondary_fill_imbalance_check(self) -> None:
        """Secondary fill blocked when it would exceed imbalance ratio."""
        config = _make_config(max_imbalance_ratio=Decimal("1.5"))
        trader = _make_trader(config=config)
        pos = _make_accum_position(
            up_price=Decimal("0.50"),
            up_qty=Decimal(10),
            down_price=Decimal("0.45"),
            down_qty=Decimal(20),
            budget=Decimal(50),
            primary_side="Up",
        )

        await trader._try_fill_secondary(pos, "Down", Decimal("0.40"), Decimal(100))
        # Down already at 20, Up at 10 → ratio 2.0 > 1.5 → blocked
        assert pos.down_leg.quantity == Decimal(20)


@pytest.mark.asyncio
class TestMultipleFills:
    """Test VWAP tracking across multiple fills."""

    async def test_multiple_fills_update_vwap(self) -> None:
        """SideLeg.add_fill produce correct weighted average across fills."""
        leg = SideLeg(
            side="Up",
            entry_price=Decimal("0.50"),
            quantity=Decimal(10),
            cost_basis=Decimal("5.00"),
        )
        leg.add_fill(Decimal("0.40"), Decimal(10))

        assert leg.entry_price == Decimal("0.4500")
        assert leg.quantity == Decimal(20)
        assert leg.cost_basis == Decimal("9.00")


@pytest.mark.asyncio
class TestSettlement:
    """Test position settlement P&L computation."""

    async def test_settlement_paired_pnl(self) -> None:
        """Paired settlement: winning_qty * 1.0 - total_cost - fees."""
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
        expected_pnl = _QTY * Decimal(1) - (_UP_PRICE * _QTY + _DOWN_PRICE * _QTY)
        assert result.pnl == expected_pnl
        assert result.pnl > Decimal(0)

    async def test_settlement_unpaired_winner(self) -> None:
        """Excess tokens on winning side add bonus profit."""
        config = _make_config(fee_rate=_ZERO_FEE)
        trader = _make_trader(config=config)
        opp = _make_opportunity(window_end_ts=_NOW - 1)
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
        expected_pnl = Decimal(15) * Decimal(1) - (
            Decimal("0.48") * Decimal(15) + Decimal("0.47") * Decimal(10)
        )
        assert result.pnl == expected_pnl

    async def test_settlement_unpaired_loser(self) -> None:
        """Excess tokens on losing side add to the loss."""
        config = _make_config(fee_rate=_ZERO_FEE)
        trader = _make_trader(config=config)
        opp = _make_opportunity(window_end_ts=_NOW - 1)
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
        assert result.pnl == _QTY * Decimal(1) - _UP_PRICE * _QTY

    async def test_settlement_only_one_side_loses(self) -> None:
        """Single-side position loses when other side wins."""
        config = _make_config(fee_rate=_ZERO_FEE)
        trader = _make_trader(config=config)
        opp = _make_opportunity(window_end_ts=_NOW - 1)
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
        assert result.pnl > Decimal(0)
        assert result.outcome_known is False


@pytest.mark.asyncio
class TestFillAgeCutoff:
    """Test that fills stop when market window is near expiry."""

    async def test_fills_blocked_past_cutoff(self) -> None:
        """No fills when market window is past max_fill_age_pct."""
        config = _make_config(max_fill_age_pct=Decimal("0.70"))
        trader = _make_trader(config=config)
        elapsed = int((_WINDOW_END - _WINDOW_START) * 0.8)
        now = _WINDOW_START + elapsed
        opp = _make_opportunity()

        assert trader._past_fill_cutoff(opp, now) is True

    async def test_fills_allowed_before_cutoff(self) -> None:
        """Fills proceed when market window is before max_fill_age_pct."""
        config = _make_config(max_fill_age_pct=Decimal("0.70"))
        trader = _make_trader(config=config)
        elapsed = int((_WINDOW_END - _WINDOW_START) * 0.5)
        now = _WINDOW_START + elapsed
        opp = _make_opportunity()

        assert trader._past_fill_cutoff(opp, now) is False

    async def test_fills_blocked_at_exact_cutoff(self) -> None:
        """Fills blocked when just past the cutoff boundary."""
        config = _make_config(max_fill_age_pct=Decimal("0.70"))
        trader = _make_trader(config=config)
        elapsed = int((_WINDOW_END - _WINDOW_START) * 0.71)
        now = _WINDOW_START + elapsed
        opp = _make_opportunity()

        assert trader._past_fill_cutoff(opp, now) is True


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
        pos = _make_accum_position(
            up_price=Decimal("0.48"),
            up_qty=_QTY,
            budget=Decimal("5.00"),
        )

        qty = trader._compute_fill_qty(pos, "Down", Decimal("0.47"), Decimal(100))
        assert qty is None

    async def test_fill_qty_capped_by_depth(self) -> None:
        """Fill qty is capped by max_book_pct of visible depth."""
        config = _make_config(fill_size_tokens=Decimal(100), max_book_pct=Decimal("0.20"))
        trader = _make_trader(config=config)
        pos = _make_accum_position(budget=Decimal(1000))

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
class TestImbalance:
    """Test imbalance ratio guard."""

    async def test_imbalance_blocks_heavy_side(self) -> None:
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

        assert trader._would_exceed_imbalance(pos, "Up", _QTY) is True
        assert trader._would_exceed_imbalance(pos, "Down", _QTY) is False

    async def test_imbalance_allows_when_other_empty(self) -> None:
        """Allow fills when the other side has no fills (can't compute ratio)."""
        config = _make_config(max_imbalance_ratio=Decimal("1.5"))
        trader = _make_trader(config=config)
        pos = _make_accum_position(
            up_price=Decimal("0.45"),
            up_qty=Decimal(20),
            budget=Decimal(50),
        )

        assert trader._would_exceed_imbalance(pos, "Up", _QTY) is False
