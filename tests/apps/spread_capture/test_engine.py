"""Tests for SpreadEngine core decision logic.

Verify the engine's poll_cycle integration: opportunity scanning,
signal determination, fill execution, and settlement via mock ports.
"""

from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock

import pytest

from trading_tools.apps.spread_capture.config import SpreadCaptureConfig
from trading_tools.apps.spread_capture.engine import SpreadEngine
from trading_tools.apps.spread_capture.models import (
    AccumulatingPosition,
    PositionState,
    SideLeg,
    SpreadOpportunity,
)
from trading_tools.apps.spread_capture.ports import FillResult
from trading_tools.clients.polymarket.models import OrderBook, OrderLevel
from trading_tools.core.models import ZERO

_WINDOW_START = 1_710_000_000
_WINDOW_END = 1_710_000_300
_NOW = 1_710_000_100
_CAPITAL = Decimal(100)
_EXPECTED_POLL_COUNT_2 = 2


def _make_config(**overrides: Any) -> SpreadCaptureConfig:
    """Create a config with accumulate strategy defaults for testing."""
    defaults: dict[str, Any] = {
        "capital": _CAPITAL,
        "max_position_pct": Decimal("0.10"),
        "max_open_positions": 10,
        "poll_interval": 5,
        "paper_slippage_pct": Decimal("0.0"),
        "fee_rate": Decimal("0.0"),
        "fee_exponent": 2,
        "strategy": "accumulate",
        "max_imbalance_ratio": Decimal("3.0"),
        "fill_size_tokens": Decimal(5),
        "initial_fill_size": Decimal(20),
        "signal_delay_seconds": 60,
        "hedge_start_threshold": Decimal("0.45"),
        "hedge_end_threshold": Decimal("0.55"),
        "hedge_start_pct": Decimal("0.20"),
        "max_fill_age_pct": Decimal("0.80"),
        "max_combined_cost": Decimal("0.98"),
        "min_spread_margin": Decimal("0.01"),
    }
    defaults.update(overrides)
    return SpreadCaptureConfig(**defaults)


def _make_opportunity(**overrides: Any) -> SpreadOpportunity:
    """Create a SpreadOpportunity with sensible defaults."""
    defaults: dict[str, Any] = {
        "condition_id": "cond_test",
        "title": "BTC Up or Down?",
        "asset": "BTC-USD",
        "up_token_id": "up_tok",
        "down_token_id": "down_tok",
        "up_price": Decimal("0.48"),
        "down_price": Decimal("0.47"),
        "combined": Decimal("0.95"),
        "margin": Decimal("0.05"),
        "window_start_ts": _WINDOW_START,
        "window_end_ts": _WINDOW_END,
        "up_ask_depth": Decimal(100),
        "down_ask_depth": Decimal(100),
    }
    defaults.update(overrides)
    return SpreadOpportunity(**defaults)


def _make_order_book(ask_price: Decimal, depth: Decimal = Decimal(100)) -> OrderBook:
    """Create a simple OrderBook with one ask level."""
    return OrderBook(
        token_id="tok",
        bids=(),
        asks=(OrderLevel(price=ask_price, size=depth),),
        spread=Decimal("0.02"),
        midpoint=ask_price,
    )


class _MockExecution:
    """Mock execution adapter that echoes fills back."""

    def __init__(self, capital: Decimal = _CAPITAL) -> None:
        """Initialize with capital.

        Args:
            capital: Starting capital.

        """
        self._capital = capital

    async def execute_fill(
        self,
        token_id: str,
        side: str,
        price: Decimal,
        quantity: Decimal,
    ) -> FillResult:
        """Return a fill at the requested price and quantity.

        Args:
            token_id: Unused.
            side: Unused.
            price: Fill price.
            quantity: Fill quantity.

        Returns:
            Echo fill result.

        """
        return FillResult(price=price, quantity=quantity)

    def get_capital(self) -> Decimal:
        """Return available capital.

        Returns:
            Capital amount.

        """
        return self._capital

    def total_capital(self) -> Decimal:
        """Return total capital.

        Returns:
            Total capital.

        """
        return self._capital


@pytest.mark.asyncio
class TestPollCycleIntegration:
    """Test the full poll_cycle flow through the engine."""

    async def test_poll_cycle_opens_position_and_fills(self) -> None:
        """Poll cycle opens a new position and attempts fills."""
        opp = _make_opportunity()
        up_book = _make_order_book(Decimal("0.48"))
        down_book = _make_order_book(Decimal("0.47"))

        mock_md = AsyncMock()
        mock_md.get_whale_signal = AsyncMock(return_value=None)
        mock_md.get_opportunities = AsyncMock(return_value=[opp])
        mock_md.get_order_books = AsyncMock(return_value=(up_book, down_book))
        mock_md.get_binance_candles = AsyncMock(
            return_value=[AsyncMock(open=Decimal(50000), close=Decimal(50100))]
        )

        engine = SpreadEngine(
            config=_make_config(),
            execution=_MockExecution(),
            market_data=mock_md,
            mode_label="TEST",
        )

        await engine.poll_cycle(_NOW)

        assert len(engine.positions) == 1
        pos = engine.positions["cond_test"]
        assert pos.state == PositionState.ACCUMULATING
        assert pos.primary_side == "Down"  # mean-reversion: up momentum → bet down
        # Primary side should have been filled
        assert pos.down_leg.quantity > ZERO

    async def test_poll_cycle_settles_expired(self) -> None:
        """Poll cycle settles positions past window_end."""
        opp = _make_opportunity(window_end_ts=_NOW - 1)

        mock_md = AsyncMock()
        mock_md.get_whale_signal = AsyncMock(return_value=None)
        mock_md.get_opportunities = AsyncMock(return_value=[])
        mock_md.resolve_outcome = AsyncMock(return_value="Up")

        engine = SpreadEngine(
            config=_make_config(),
            execution=_MockExecution(),
            market_data=mock_md,
            mode_label="TEST",
        )

        # Manually insert a position to settle
        pos = AccumulatingPosition(
            opportunity=opp,
            state=PositionState.ACCUMULATING,
            up_leg=SideLeg("Up", Decimal("0.48"), Decimal(10), Decimal("4.80")),
            down_leg=SideLeg("Down", Decimal("0.47"), Decimal(10), Decimal("4.70")),
            entry_time=_WINDOW_START,
            budget=Decimal(50),
            primary_side="Up",
        )
        engine._positions[opp.condition_id] = pos

        await engine.poll_cycle(_NOW)

        assert len(engine.positions) == 0
        assert len(engine.results) == 1
        assert engine.results[0].pnl > ZERO

    async def test_poll_cycle_respects_max_open(self) -> None:
        """Engine does not open more positions than max_open_positions."""
        config = _make_config(max_open_positions=1)
        opp1 = _make_opportunity(condition_id="cond_1")
        opp2 = _make_opportunity(condition_id="cond_2")

        mock_md = AsyncMock()
        mock_md.get_whale_signal = AsyncMock(return_value=None)
        mock_md.get_opportunities = AsyncMock(return_value=[opp1, opp2])

        engine = SpreadEngine(
            config=config,
            execution=_MockExecution(),
            market_data=mock_md,
            mode_label="TEST",
        )

        await engine.poll_cycle(_NOW)

        assert len(engine.positions) == 1

    async def test_poll_count_increments(self) -> None:
        """Each poll_cycle increments the poll counter."""
        mock_md = AsyncMock()
        mock_md.get_whale_signal = AsyncMock(return_value=None)
        mock_md.get_opportunities = AsyncMock(return_value=[])

        engine = SpreadEngine(
            config=_make_config(),
            execution=_MockExecution(),
            market_data=mock_md,
            mode_label="TEST",
        )

        assert engine.poll_count == 0
        await engine.poll_cycle(_NOW)
        assert engine.poll_count == 1
        await engine.poll_cycle(_NOW)
        assert engine.poll_count == _EXPECTED_POLL_COUNT_2
