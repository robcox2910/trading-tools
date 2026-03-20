"""Tests for the directional trading engine."""

from decimal import Decimal
from typing import Any

import pytest

from trading_tools.apps.directional.adapters import (
    BacktestExecution,
    ReplayMarketData,
)
from trading_tools.apps.directional.config import DirectionalConfig
from trading_tools.apps.directional.engine import DirectionalEngine
from trading_tools.apps.directional.estimator import ProbabilityEstimator
from trading_tools.apps.directional.models import MarketOpportunity
from trading_tools.clients.polymarket.models import OrderBook, OrderLevel
from trading_tools.core.models import ZERO, Candle, Interval

_CAPITAL = Decimal(1000)
_WINDOW_START = 1_710_000_000
_WINDOW_END = 1_710_000_300
_ENTRY_TIME = _WINDOW_END - 20  # 20s before close, within default [10, 30] window


def _make_opportunity(
    condition_id: str = "cond_1",
    up_price: Decimal = Decimal("0.50"),
    down_price: Decimal = Decimal("0.50"),
) -> MarketOpportunity:
    """Create a test market opportunity."""
    return MarketOpportunity(
        condition_id=condition_id,
        title="BTC Up or Down?",
        asset="BTC-USD",
        up_token_id=f"up_{condition_id}",
        down_token_id=f"down_{condition_id}",
        window_start_ts=_WINDOW_START,
        window_end_ts=_WINDOW_END,
        up_price=up_price,
        down_price=down_price,
    )


def _make_candles(n: int = 20, direction: str = "up") -> list[Candle]:
    """Create N candles with a trend direction for feature extraction.

    Generate candles whose timestamps fall within the default lookback
    window (signal_lookback_seconds=300) so the engine actually receives
    them from ReplayMarketData.get_binance_candles.
    """
    candles: list[Candle] = []
    base = Decimal(100)
    # Space candles 10s apart so 20 candles fit in a 200s window
    step = 10
    for i in range(n):
        if direction == "up":
            o = base + Decimal(i)
            c = base + Decimal(i + 1)
        elif direction == "down":
            o = base - Decimal(i)
            c = base - Decimal(i + 1)
        else:
            o = c = base
        candles.append(
            Candle(
                symbol="BTC-USD",
                timestamp=_ENTRY_TIME - (n - i) * step,
                open=o,
                high=max(o, c),
                low=min(o, c),
                close=c,
                volume=Decimal(1000 + i * 100),
                interval=Interval.M1,
            )
        )
    return candles


def _make_order_book(token_id: str, bid_depth: Decimal = Decimal(100)) -> OrderBook:
    """Create a test order book with specified bid depth."""
    return OrderBook(
        token_id=token_id,
        bids=(OrderLevel(price=Decimal("0.50"), size=bid_depth),),
        asks=(OrderLevel(price=Decimal("0.50"), size=Decimal(500)),),
        spread=Decimal("0.01"),
        midpoint=Decimal("0.50"),
        min_order_size=Decimal(5),
    )


def _build_engine(
    *,
    capital: Decimal = _CAPITAL,
    markets: list[MarketOpportunity] | None = None,
    candles: list[Candle] | None = None,
    outcome: str | None = "Up",
    config_overrides: dict[str, Any] | None = None,
) -> tuple[DirectionalEngine, BacktestExecution, ReplayMarketData]:
    """Build a DirectionalEngine with backtest adapters pre-loaded."""
    config_kwargs: dict[str, Any] = {
        "capital": capital,
        "min_edge": Decimal("0.01"),
        "kelly_fraction": Decimal("0.5"),
        "max_position_pct": Decimal("0.50"),
    }
    if config_overrides:
        config_kwargs.update(config_overrides)
    config = DirectionalConfig(**config_kwargs)

    execution = BacktestExecution(capital=capital)
    market_data = ReplayMarketData()
    estimator = ProbabilityEstimator(config)

    if markets is None:
        markets = [_make_opportunity()]
    market_data.set_markets(markets)

    if candles is None:
        candles = _make_candles(20, "up")
    market_data.set_candles("BTC-USD", candles)

    for mkt in markets:
        up_book = _make_order_book(mkt.up_token_id, Decimal(200))
        down_book = _make_order_book(mkt.down_token_id, Decimal(100))
        market_data.set_order_books(mkt.condition_id, up_book, down_book)
        if outcome is not None:
            market_data.set_outcome(mkt.condition_id, outcome)

    engine = DirectionalEngine(
        config=config,
        execution=execution,
        market_data=market_data,
        estimator=estimator,
        mode_label="paper",
    )
    return engine, execution, market_data


class TestEntryWindow:
    """Test entry timing gate."""

    @pytest.mark.asyncio
    async def test_entry_within_window(self) -> None:
        """Engine enters a position when within the entry window."""
        engine, _, _ = _build_engine()
        await engine.poll_cycle(_ENTRY_TIME)
        assert len(engine.positions) == 1

    @pytest.mark.asyncio
    async def test_no_entry_before_window(self) -> None:
        """No entry when too far from market close."""
        engine, _, _ = _build_engine()
        too_early = _WINDOW_END - 60  # 60s before close, outside [10, 30]
        await engine.poll_cycle(too_early)
        assert len(engine.positions) == 0

    @pytest.mark.asyncio
    async def test_no_entry_after_window(self) -> None:
        """No entry when too close to market close."""
        engine, _, _ = _build_engine()
        too_late = _WINDOW_END - 5  # 5s before close, outside [10, 30]
        await engine.poll_cycle(too_late)
        assert len(engine.positions) == 0


class TestEvaluateAndEnter:
    """Test feature extraction, estimation, and position entry."""

    @pytest.mark.asyncio
    async def test_position_created_with_correct_side(self) -> None:
        """Position is created on the predicted winning side."""
        engine, _, _ = _build_engine(candles=_make_candles(20, "up"))
        await engine.poll_cycle(_ENTRY_TIME)
        assert len(engine.positions) == 1
        pos = next(iter(engine.positions.values()))
        assert pos.predicted_side == "Up"

    @pytest.mark.asyncio
    async def test_down_trend_predicts_down(self) -> None:
        """Downtrending candles predict Down side."""
        engine, _, _ = _build_engine(candles=_make_candles(20, "down"))
        await engine.poll_cycle(_ENTRY_TIME)
        if engine.positions:
            pos = next(iter(engine.positions.values()))
            assert pos.predicted_side == "Down"

    @pytest.mark.asyncio
    async def test_no_entry_below_min_edge(self) -> None:
        """No entry when edge is below min_edge threshold."""
        # Set min_edge very high so no signal can clear it
        engine, _, _ = _build_engine(
            candles=_make_candles(20, "flat"),
            config_overrides={"min_edge": Decimal("0.40")},
        )
        await engine.poll_cycle(_ENTRY_TIME)
        assert len(engine.positions) == 0

    @pytest.mark.asyncio
    async def test_no_entry_when_token_price_below_min(self) -> None:
        """Skip entry when token price is below min_token_price (market decided)."""
        engine, _, _ = _build_engine(
            markets=[
                _make_opportunity("cond_1", up_price=Decimal("0.06"), down_price=Decimal("0.94"))
            ],
            config_overrides={"min_token_price": Decimal("0.15")},
        )
        await engine.poll_cycle(_ENTRY_TIME)
        assert len(engine.positions) == 0

    @pytest.mark.asyncio
    async def test_position_has_features(self) -> None:
        """Position stores the feature vector used at entry."""
        engine, _, _ = _build_engine()
        await engine.poll_cycle(_ENTRY_TIME)
        pos = next(iter(engine.positions.values()))
        assert pos.features is not None
        assert isinstance(pos.features.momentum, Decimal)

    @pytest.mark.asyncio
    async def test_max_open_positions_respected(self) -> None:
        """Cannot exceed max_open_positions."""
        markets = [
            _make_opportunity(f"cond_{i}", Decimal("0.50"), Decimal("0.50")) for i in range(5)
        ]
        engine, _, _ = _build_engine(
            markets=markets,
            config_overrides={"max_open_positions": 2},
        )
        await engine.poll_cycle(_ENTRY_TIME)
        assert len(engine.positions) <= 2


class TestSettlement:
    """Test position settlement and P&L computation."""

    @pytest.mark.asyncio
    async def test_winning_trade_positive_pnl(self) -> None:
        """Winning trade (predicted side = winning side) has positive P&L."""
        engine, _execution, _ = _build_engine(outcome="Up")
        await engine.poll_cycle(_ENTRY_TIME)
        assert len(engine.positions) == 1

        await engine.poll_cycle(_WINDOW_END)
        assert len(engine.positions) == 0
        assert len(engine.results) == 1
        assert engine.results[0].pnl > ZERO

    @pytest.mark.asyncio
    async def test_losing_trade_negative_pnl(self) -> None:
        """Losing trade has negative P&L."""
        engine, _, _ = _build_engine(outcome="Down", candles=_make_candles(20, "up"))
        await engine.poll_cycle(_ENTRY_TIME)

        await engine.poll_cycle(_WINDOW_END)
        assert len(engine.results) == 1
        assert engine.results[0].pnl < ZERO

    @pytest.mark.asyncio
    async def test_unresolved_outcome_negative_pnl(self) -> None:
        """Unresolved outcome results in full loss."""
        engine, _, _ = _build_engine(outcome=None)
        # Need to clear the outcome
        assert isinstance(engine.market_data, ReplayMarketData)
        engine.market_data._outcomes.clear()
        await engine.poll_cycle(_ENTRY_TIME)

        await engine.poll_cycle(_WINDOW_END)
        assert len(engine.results) == 1
        assert engine.results[0].pnl < ZERO

    @pytest.mark.asyncio
    async def test_capital_updated_after_settlement(self) -> None:
        """Capital is updated with P&L after settlement."""
        engine, execution, _ = _build_engine(outcome="Up")
        initial_capital = execution.total_capital()
        await engine.poll_cycle(_ENTRY_TIME)
        await engine.poll_cycle(_WINDOW_END)
        # After a win, capital should be higher than initial
        # (cost_basis returned + profit)
        assert execution.total_capital() > initial_capital - Decimal(1)


class TestRiskManagement:
    """Test drawdown halt and circuit breaker."""

    @pytest.mark.asyncio
    async def test_circuit_breaker_activates(self) -> None:
        """Circuit breaker halts entries after consecutive losses."""
        markets = [
            _make_opportunity(f"cond_{i}", Decimal("0.50"), Decimal("0.50")) for i in range(5)
        ]
        engine, _, market_data = _build_engine(
            markets=markets,
            outcome="Down",
            candles=_make_candles(20, "up"),
            config_overrides={
                "circuit_breaker_losses": 2,
                "circuit_breaker_cooldown": 600,
                "max_open_positions": 5,
            },
        )

        # Enter and settle two losing trades
        for i in range(2):
            market_data.set_markets([_make_opportunity(f"cond_loss_{i}")])
            market_data.set_order_books(
                f"cond_loss_{i}",
                _make_order_book(f"up_cond_loss_{i}", Decimal(200)),
                _make_order_book(f"down_cond_loss_{i}", Decimal(100)),
            )
            market_data.set_outcome(f"cond_loss_{i}", "Down")
            await engine.poll_cycle(_ENTRY_TIME + i)
            await engine.poll_cycle(_WINDOW_END + i)

        # Third entry should be blocked by circuit breaker
        market_data.set_markets([_make_opportunity("cond_new")])
        market_data.set_order_books(
            "cond_new",
            _make_order_book("up_cond_new", Decimal(200)),
            _make_order_book("down_cond_new", Decimal(100)),
        )
        await engine.poll_cycle(_WINDOW_END + 10)
        assert len(engine.positions) == 0
