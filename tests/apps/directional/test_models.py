"""Tests for directional trading data models."""

from decimal import Decimal

import pytest

from trading_tools.apps.directional.models import (
    DirectionalPosition,
    DirectionalResult,
    DirectionalResultRecord,
    FeatureVector,
    MarketOpportunity,
)

_UP_PRICE = Decimal("0.55")
_DOWN_PRICE = Decimal("0.45")
_QTY = Decimal(20)
_FEE = Decimal("0.02")
_WINDOW_START = 1_710_000_000
_WINDOW_END = 1_710_000_300
_ENTRY_TIME = 1_710_000_270
_SETTLED_AT = 1_710_000_300
_PNL = Decimal("7.00")


def _make_opportunity(**overrides: object) -> MarketOpportunity:
    """Create a MarketOpportunity with sensible defaults."""
    defaults: dict[str, object] = {
        "condition_id": "cond_btc_001",
        "title": "Bitcoin Up or Down?",
        "asset": "BTC-USD",
        "up_token_id": "up_tok_1",
        "down_token_id": "down_tok_1",
        "window_start_ts": _WINDOW_START,
        "window_end_ts": _WINDOW_END,
        "up_price": _UP_PRICE,
        "down_price": _DOWN_PRICE,
    }
    defaults.update(overrides)
    return MarketOpportunity(**defaults)  # type: ignore[arg-type]


def _make_features(**overrides: object) -> FeatureVector:
    """Create a FeatureVector with sensible defaults."""
    defaults: dict[str, object] = {
        "momentum": Decimal("0.30"),
        "volatility_regime": Decimal("-0.10"),
        "volume_profile": Decimal("0.20"),
        "book_imbalance": Decimal("0.15"),
        "rsi_signal": Decimal("0.05"),
        "price_change_pct": Decimal("0.25"),
    }
    defaults.update(overrides)
    return FeatureVector(**defaults)  # type: ignore[arg-type]


class TestMarketOpportunity:
    """Test MarketOpportunity frozen dataclass."""

    def test_basic_creation(self) -> None:
        """Create an opportunity with all required fields."""
        opp = _make_opportunity()
        assert opp.condition_id == "cond_btc_001"
        assert opp.asset == "BTC-USD"
        assert opp.up_price == _UP_PRICE
        assert opp.down_price == _DOWN_PRICE

    def test_frozen(self) -> None:
        """Opportunity is immutable."""
        opp = _make_opportunity()
        with pytest.raises(AttributeError):
            opp.up_price = Decimal("0.99")  # type: ignore[misc]

    def test_default_depth_is_zero(self) -> None:
        """Ask depth defaults to zero when not provided."""
        opp = _make_opportunity()
        assert opp.up_ask_depth == Decimal(0)
        assert opp.down_ask_depth == Decimal(0)

    def test_custom_depth(self) -> None:
        """Ask depth can be overridden."""
        opp = _make_opportunity(up_ask_depth=Decimal(500), down_ask_depth=Decimal(300))
        assert opp.up_ask_depth == Decimal(500)
        assert opp.down_ask_depth == Decimal(300)


class TestFeatureVector:
    """Test FeatureVector frozen dataclass."""

    def test_basic_creation(self) -> None:
        """Create a feature vector with all features."""
        feat = _make_features()
        assert feat.momentum == Decimal("0.30")
        assert feat.volatility_regime == Decimal("-0.10")

    def test_frozen(self) -> None:
        """Feature vector is immutable."""
        feat = _make_features()
        with pytest.raises(AttributeError):
            feat.momentum = Decimal("0.99")  # type: ignore[misc]


class TestDirectionalPosition:
    """Test DirectionalPosition mutable dataclass."""

    def test_basic_creation(self) -> None:
        """Create an open directional position."""
        opp = _make_opportunity()
        feat = _make_features()
        pos = DirectionalPosition(
            opportunity=opp,
            predicted_side="Up",
            p_up=Decimal("0.65"),
            token_id="up_tok_1",
            entry_price=_UP_PRICE,
            quantity=_QTY,
            cost_basis=_UP_PRICE * _QTY + _FEE,
            fee=_FEE,
            entry_time=_ENTRY_TIME,
            features=feat,
        )
        assert pos.predicted_side == "Up"
        assert pos.is_paper is True

    def test_mutable(self) -> None:
        """Position fields can be updated."""
        opp = _make_opportunity()
        feat = _make_features()
        pos = DirectionalPosition(
            opportunity=opp,
            predicted_side="Up",
            p_up=Decimal("0.65"),
            token_id="up_tok_1",
            entry_price=_UP_PRICE,
            quantity=_QTY,
            cost_basis=_UP_PRICE * _QTY + _FEE,
            fee=_FEE,
            entry_time=_ENTRY_TIME,
            features=feat,
        )
        pos.quantity = Decimal(30)
        assert pos.quantity == Decimal(30)


class TestDirectionalResult:
    """Test DirectionalResult frozen dataclass."""

    def test_basic_creation(self) -> None:
        """Create a closed directional result with P&L."""
        opp = _make_opportunity()
        feat = _make_features()
        result = DirectionalResult(
            opportunity=opp,
            predicted_side="Up",
            winning_side="Up",
            p_up=Decimal("0.65"),
            token_id="up_tok_1",
            entry_price=_UP_PRICE,
            quantity=_QTY,
            cost_basis=_UP_PRICE * _QTY + _FEE,
            fee=_FEE,
            entry_time=_ENTRY_TIME,
            settled_at=_SETTLED_AT,
            pnl=_PNL,
            features=feat,
        )
        assert result.pnl == _PNL
        assert result.winning_side == "Up"
        assert result.is_paper is True

    def test_frozen(self) -> None:
        """Result is immutable."""
        opp = _make_opportunity()
        feat = _make_features()
        result = DirectionalResult(
            opportunity=opp,
            predicted_side="Up",
            winning_side="Up",
            p_up=Decimal("0.65"),
            token_id="up_tok_1",
            entry_price=_UP_PRICE,
            quantity=_QTY,
            cost_basis=_UP_PRICE * _QTY + _FEE,
            fee=_FEE,
            entry_time=_ENTRY_TIME,
            settled_at=_SETTLED_AT,
            pnl=_PNL,
            features=feat,
        )
        with pytest.raises(AttributeError):
            result.pnl = Decimal(0)  # type: ignore[misc]


class TestDirectionalResultRecord:
    """Test ORM record creation from DirectionalResult."""

    def test_from_result(self) -> None:
        """Convert a DirectionalResult to an ORM record with correct field mapping."""
        opp = _make_opportunity()
        feat = _make_features()
        result = DirectionalResult(
            opportunity=opp,
            predicted_side="Up",
            winning_side="Up",
            p_up=Decimal("0.65"),
            token_id="up_tok_1",
            entry_price=_UP_PRICE,
            quantity=_QTY,
            cost_basis=_UP_PRICE * _QTY + _FEE,
            fee=_FEE,
            entry_time=_ENTRY_TIME,
            settled_at=_SETTLED_AT,
            pnl=_PNL,
            features=feat,
            order_id="order_123",
        )
        record = DirectionalResultRecord.from_result(result)
        assert record.condition_id == "cond_btc_001"
        assert record.asset == "BTC-USD"
        assert record.predicted_side == "Up"
        assert record.winning_side == "Up"
        assert record.p_up == float(Decimal("0.65"))
        assert record.pnl == float(_PNL)
        assert record.order_id == "order_123"
        assert record.f_momentum == float(Decimal("0.30"))
        assert record.f_volatility == float(Decimal("-0.10"))
        assert record.f_book_imbalance == float(Decimal("0.15"))
        assert record.is_paper is True
