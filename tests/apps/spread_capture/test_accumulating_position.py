"""Tests for AccumulatingPosition model properties."""

from decimal import Decimal

from trading_tools.apps.spread_capture.models import (
    AccumulatingPosition,
    PositionState,
    SideLeg,
    SpreadOpportunity,
)

_WINDOW_START = 1_710_000_000
_WINDOW_END = 1_710_000_300
_NOW = 1_710_000_100
_EXPECTED_COMBINED_VWAP = Decimal("0.9200")
_EXPECTED_RATIO_TWO = Decimal(2)


def _make_opportunity(**overrides: object) -> SpreadOpportunity:
    """Create a SpreadOpportunity with sensible defaults."""
    defaults: dict[str, object] = {
        "condition_id": "cond_a",
        "title": "Bitcoin Up or Down?",
        "asset": "BTC-USD",
        "up_token_id": "up_tok_1",
        "down_token_id": "down_tok_1",
        "up_price": Decimal("0.48"),
        "down_price": Decimal("0.47"),
        "combined": Decimal("0.95"),
        "margin": Decimal("0.05"),
        "window_start_ts": _WINDOW_START,
        "window_end_ts": _WINDOW_END,
    }
    defaults.update(overrides)
    return SpreadOpportunity(**defaults)  # type: ignore[arg-type]


def _make_position(
    up_price: Decimal = Decimal(0),
    up_qty: Decimal = Decimal(0),
    down_price: Decimal = Decimal(0),
    down_qty: Decimal = Decimal(0),
) -> AccumulatingPosition:
    """Create an AccumulatingPosition with specified leg values."""
    return AccumulatingPosition(
        opportunity=_make_opportunity(),
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
    )


class TestCombinedVwap:
    """Test combined_vwap property."""

    def test_zero_when_no_fills(self) -> None:
        """Return zero when neither side has fills."""
        pos = _make_position()
        assert pos.combined_vwap == Decimal(0)

    def test_zero_when_only_up_has_fills(self) -> None:
        """Return zero when only Up side has fills."""
        pos = _make_position(up_price=Decimal("0.48"), up_qty=Decimal(10))
        assert pos.combined_vwap == Decimal(0)

    def test_zero_when_only_down_has_fills(self) -> None:
        """Return zero when only Down side has fills."""
        pos = _make_position(down_price=Decimal("0.47"), down_qty=Decimal(10))
        assert pos.combined_vwap == Decimal(0)

    def test_combined_vwap_both_sides(self) -> None:
        """Return sum of entry prices when both sides have fills."""
        pos = _make_position(
            up_price=Decimal("0.48"),
            up_qty=Decimal(10),
            down_price=Decimal("0.44"),
            down_qty=Decimal(10),
        )
        assert pos.combined_vwap == _EXPECTED_COMBINED_VWAP

    def test_vwap_updates_after_add_fill(self) -> None:
        """VWAP updates correctly after adding a fill via SideLeg.add_fill."""
        pos = _make_position(
            up_price=Decimal("0.48"),
            up_qty=Decimal(10),
            down_price=Decimal("0.44"),
            down_qty=Decimal(10),
        )
        # Add a cheaper fill to the Up side
        pos.up_leg.add_fill(Decimal("0.40"), Decimal(10))
        # New Up VWAP = (4.8 + 4.0) / 20 = 0.44
        assert pos.up_leg.entry_price == Decimal("0.4400")
        # Combined = 0.44 + 0.44 = 0.88
        assert pos.combined_vwap == Decimal("0.8800")


class TestPairedQuantity:
    """Test paired_quantity property."""

    def test_zero_when_no_fills(self) -> None:
        """Return zero when neither side has fills."""
        pos = _make_position()
        assert pos.paired_quantity == Decimal(0)

    def test_min_of_both_sides(self) -> None:
        """Return minimum of both side quantities."""
        pos = _make_position(
            up_price=Decimal("0.48"),
            up_qty=Decimal(20),
            down_price=Decimal("0.44"),
            down_qty=Decimal(10),
        )
        assert pos.paired_quantity == Decimal(10)

    def test_equal_quantities(self) -> None:
        """Return the quantity when both sides are equal."""
        pos = _make_position(
            up_price=Decimal("0.48"),
            up_qty=Decimal(15),
            down_price=Decimal("0.44"),
            down_qty=Decimal(15),
        )
        assert pos.paired_quantity == Decimal(15)


class TestTotalCostBasis:
    """Test total_cost_basis property."""

    def test_zero_when_no_fills(self) -> None:
        """Return zero when neither side has fills."""
        pos = _make_position()
        assert pos.total_cost_basis == Decimal(0)

    def test_sum_of_both_sides(self) -> None:
        """Return sum of cost bases from both legs."""
        pos = _make_position(
            up_price=Decimal("0.48"),
            up_qty=Decimal(10),
            down_price=Decimal("0.44"),
            down_qty=Decimal(10),
        )
        # 0.48 * 10 + 0.44 * 10 = 4.8 + 4.4 = 9.2
        assert pos.total_cost_basis == Decimal("9.2")


class TestImbalanceRatio:
    """Test imbalance_ratio property."""

    def test_zero_when_no_fills(self) -> None:
        """Return zero when neither side has fills."""
        pos = _make_position()
        assert pos.imbalance_ratio == Decimal(0)

    def test_zero_when_one_side_empty(self) -> None:
        """Return zero when only one side has fills."""
        pos = _make_position(up_price=Decimal("0.48"), up_qty=Decimal(10))
        assert pos.imbalance_ratio == Decimal(0)

    def test_one_when_equal(self) -> None:
        """Return 1.0 when both sides have equal quantities."""
        pos = _make_position(
            up_price=Decimal("0.48"),
            up_qty=Decimal(10),
            down_price=Decimal("0.44"),
            down_qty=Decimal(10),
        )
        assert pos.imbalance_ratio == Decimal(1)

    def test_ratio_when_unequal(self) -> None:
        """Return max/min ratio when sides are unequal."""
        pos = _make_position(
            up_price=Decimal("0.48"),
            up_qty=Decimal(20),
            down_price=Decimal("0.44"),
            down_qty=Decimal(10),
        )
        assert pos.imbalance_ratio == _EXPECTED_RATIO_TWO


class TestAllOrderIds:
    """Test all_order_ids property."""

    def test_empty_when_no_orders(self) -> None:
        """Return empty list when no orders exist."""
        pos = _make_position()
        assert pos.all_order_ids == []

    def test_combines_both_legs(self) -> None:
        """Return order IDs from both legs."""
        pos = _make_position(
            up_price=Decimal("0.48"),
            up_qty=Decimal(10),
            down_price=Decimal("0.44"),
            down_qty=Decimal(10),
        )
        pos.up_leg.order_ids.append("order_up_1")
        pos.down_leg.order_ids.append("order_down_1")
        assert set(pos.all_order_ids) == {"order_up_1", "order_down_1"}


class TestPrimarySide:
    """Test primary_side attribute."""

    def test_default_is_none(self) -> None:
        """Primary side is None by default before signal determination."""
        pos = _make_position()
        assert pos.primary_side is None

    def test_can_set_primary_side(self) -> None:
        """Primary side can be set to Up or Down after signal."""
        pos = _make_position()
        pos.primary_side = "Up"
        assert pos.primary_side == "Up"

        pos.primary_side = "Down"
        assert pos.primary_side == "Down"
