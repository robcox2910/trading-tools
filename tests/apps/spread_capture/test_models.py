"""Tests for spread capture data models."""

from decimal import Decimal
from typing import Any

import pytest

from trading_tools.apps.spread_capture.models import (
    PairedPosition,
    PositionState,
    SideLeg,
    SpreadOpportunity,
    SpreadResult,
    SpreadResultRecord,
)

_UP_PRICE = Decimal("0.48")
_DOWN_PRICE = Decimal("0.47")
_COMBINED = _UP_PRICE + _DOWN_PRICE
_MARGIN = Decimal(1) - _COMBINED
_QTY = Decimal(10)
_WINDOW_START = 1_710_000_000
_WINDOW_END = 1_710_000_300
_NOW = 1_710_000_100


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


class TestSpreadOpportunity:
    """Test SpreadOpportunity frozen dataclass."""

    def test_margin_calculation(self) -> None:
        """Margin is 1.0 minus combined cost."""
        opp = _make_opportunity()
        assert opp.margin == Decimal(1) - (opp.up_price + opp.down_price)

    def test_frozen(self) -> None:
        """Opportunity is immutable."""
        opp = _make_opportunity()
        with pytest.raises(AttributeError):
            opp.up_price = Decimal("0.99")  # type: ignore[misc]


class TestSideLeg:
    """Test SideLeg mutable position tracking."""

    def test_basic_creation(self) -> None:
        """Create a side leg with initial values."""
        leg = SideLeg(side="Up", entry_price=_UP_PRICE, quantity=_QTY, cost_basis=_UP_PRICE * _QTY)
        assert leg.side == "Up"
        assert leg.quantity == _QTY

    def test_add_fill_updates_weighted_average(self) -> None:
        """Adding a fill updates the weighted-average entry price."""
        leg = SideLeg(
            side="Up",
            entry_price=Decimal("0.40"),
            quantity=Decimal(10),
            cost_basis=Decimal("4.00"),
        )
        leg.add_fill(Decimal("0.50"), Decimal(10))
        assert leg.quantity == Decimal(20)
        assert leg.cost_basis == Decimal("9.00")
        expected_price = (Decimal("9.00") / Decimal(20)).quantize(Decimal("0.0001"))
        assert leg.entry_price == expected_price

    def test_empty_order_ids_by_default(self) -> None:
        """Order IDs list starts empty."""
        leg = SideLeg(
            side="Down", entry_price=_DOWN_PRICE, quantity=_QTY, cost_basis=_DOWN_PRICE * _QTY
        )
        assert leg.order_ids == []


class TestPairedPosition:
    """Test PairedPosition state transitions and properties."""

    def _make_position(self, *, with_down: bool = True) -> PairedPosition:
        """Create a PairedPosition for testing."""
        opp = _make_opportunity()
        up_leg = SideLeg(
            side="Up", entry_price=_UP_PRICE, quantity=_QTY, cost_basis=_UP_PRICE * _QTY
        )
        down_leg = (
            SideLeg(
                side="Down",
                entry_price=_DOWN_PRICE,
                quantity=_QTY,
                cost_basis=_DOWN_PRICE * _QTY,
            )
            if with_down
            else None
        )
        state = PositionState.PAIRED if with_down else PositionState.SINGLE_LEG
        return PairedPosition(
            opportunity=opp,
            state=state,
            up_leg=up_leg,
            down_leg=down_leg,
            entry_time=_NOW,
        )

    def test_total_cost_basis_paired(self) -> None:
        """Total cost basis includes both legs."""
        pos = self._make_position()
        expected = _UP_PRICE * _QTY + _DOWN_PRICE * _QTY
        assert pos.total_cost_basis == expected

    def test_total_cost_basis_single_leg(self) -> None:
        """Single-leg total cost is just the up leg."""
        pos = self._make_position(with_down=False)
        assert pos.total_cost_basis == _UP_PRICE * _QTY

    def test_is_paired_true(self) -> None:
        """Return True when both legs are filled and state is PAIRED."""
        pos = self._make_position()
        assert pos.is_paired is True

    def test_is_paired_false_single_leg(self) -> None:
        """Return False when only one leg is filled."""
        pos = self._make_position(with_down=False)
        assert pos.is_paired is False

    def test_all_order_ids(self) -> None:
        """Collect order IDs from both legs."""
        pos = self._make_position()
        pos.up_leg.order_ids.append("order_1")
        assert pos.down_leg is not None
        pos.down_leg.order_ids.append("order_2")
        assert pos.all_order_ids == ["order_1", "order_2"]


class TestPositionState:
    """Test PositionState enum values."""

    def test_pending_value(self) -> None:
        """PENDING state has correct string value."""
        assert PositionState.PENDING.value == "pending"

    def test_paired_value(self) -> None:
        """PAIRED state has correct string value."""
        assert PositionState.PAIRED.value == "paired"

    def test_single_leg_value(self) -> None:
        """SINGLE_LEG state has correct string value."""
        assert PositionState.SINGLE_LEG.value == "single_leg"

    def test_settled_value(self) -> None:
        """SETTLED state has correct string value."""
        assert PositionState.SETTLED.value == "settled"


class TestSpreadResult:
    """Test SpreadResult frozen dataclass."""

    def test_basic_creation(self) -> None:
        """Create a spread result with computed fields."""
        opp = _make_opportunity()
        result = SpreadResult(
            opportunity=opp,
            state=PositionState.SETTLED,
            up_entry=_UP_PRICE,
            up_qty=_QTY,
            down_entry=_DOWN_PRICE,
            down_qty=_QTY,
            total_cost_basis=_COMBINED * _QTY,
            entry_time=_NOW,
            exit_time=_WINDOW_END,
            winning_side="Up",
            pnl=_QTY * _MARGIN,
            is_paper=True,
            order_ids=("ord_1", "ord_2"),
        )
        assert result.pnl == _QTY * _MARGIN
        assert result.outcome_known is True


class TestSpreadResultRecord:
    """Test ORM record creation from SpreadResult."""

    def test_from_spread_result(self) -> None:
        """Convert a SpreadResult to an ORM record with correct field mapping."""
        opp = _make_opportunity()
        pnl = _QTY * _MARGIN
        result = SpreadResult(
            opportunity=opp,
            state=PositionState.SETTLED,
            up_entry=_UP_PRICE,
            up_qty=_QTY,
            down_entry=_DOWN_PRICE,
            down_qty=_QTY,
            total_cost_basis=_COMBINED * _QTY,
            entry_time=_NOW,
            exit_time=_WINDOW_END,
            winning_side="Up",
            pnl=pnl,
            is_paper=True,
            order_ids=("ord_1", "ord_2"),
        )
        record = SpreadResultRecord.from_spread_result(result)
        assert record.condition_id == "cond_a"
        assert record.asset == "BTC-USD"
        assert record.combined_price == float(_COMBINED)
        assert record.margin == float(_MARGIN)
        assert record.pnl == float(pnl)
        assert record.order_ids == "ord_1,ord_2"
        assert record.is_paper is True
