"""Tests for Polymarket bot data models."""

from decimal import Decimal

import pytest

from trading_tools.apps.polymarket_bot.models import (
    BotConfig,
    MarketSnapshot,
    PaperTrade,
    PaperTradingResult,
)
from trading_tools.clients.polymarket.models import OrderBook
from trading_tools.core.models import ZERO, Side

_CONDITION_ID = "cond_abc123"
_QUESTION = "Will BTC reach $200K?"
_TIMESTAMP = 1700000000
_YES_PRICE = Decimal("0.65")
_NO_PRICE = Decimal("0.35")
_VOLUME = Decimal(50000)
_LIQUIDITY = Decimal(10000)
_END_DATE = "2026-12-31"


def _empty_order_book() -> OrderBook:
    """Create an empty order book for testing."""
    return OrderBook(token_id="tok1", bids=(), asks=(), spread=ZERO, midpoint=ZERO)


def _snapshot(**kwargs: object) -> MarketSnapshot:
    """Create a MarketSnapshot with sensible defaults.

    Args:
        **kwargs: Override any default field values.

    Returns:
        MarketSnapshot instance.

    """
    defaults: dict[str, object] = {
        "condition_id": _CONDITION_ID,
        "question": _QUESTION,
        "timestamp": _TIMESTAMP,
        "yes_price": _YES_PRICE,
        "no_price": _NO_PRICE,
        "order_book": _empty_order_book(),
        "volume": _VOLUME,
        "liquidity": _LIQUIDITY,
        "end_date": _END_DATE,
    }
    defaults.update(kwargs)
    return MarketSnapshot(**defaults)  # type: ignore[arg-type]


class TestMarketSnapshot:
    """Tests for MarketSnapshot frozen dataclass."""

    def test_create_valid_snapshot(self) -> None:
        """Test creating a snapshot with valid data."""
        snap = _snapshot()
        assert snap.condition_id == _CONDITION_ID
        assert snap.yes_price == _YES_PRICE
        assert snap.no_price == _NO_PRICE
        assert snap.timestamp == _TIMESTAMP

    def test_frozen(self) -> None:
        """Test that MarketSnapshot is immutable."""
        snap = _snapshot()
        with pytest.raises(AttributeError):
            snap.yes_price = Decimal("0.5")  # type: ignore[misc]

    def test_yes_price_below_zero_raises(self) -> None:
        """Test that yes_price below 0 raises ValueError."""
        with pytest.raises(ValueError, match="yes_price must be between 0 and 1"):
            _snapshot(yes_price=Decimal("-0.01"))

    def test_yes_price_above_one_raises(self) -> None:
        """Test that yes_price above 1 raises ValueError."""
        with pytest.raises(ValueError, match="yes_price must be between 0 and 1"):
            _snapshot(yes_price=Decimal("1.01"))

    def test_no_price_below_zero_raises(self) -> None:
        """Test that no_price below 0 raises ValueError."""
        with pytest.raises(ValueError, match="no_price must be between 0 and 1"):
            _snapshot(no_price=Decimal("-0.1"))

    def test_no_price_above_one_raises(self) -> None:
        """Test that no_price above 1 raises ValueError."""
        with pytest.raises(ValueError, match="no_price must be between 0 and 1"):
            _snapshot(no_price=Decimal("1.5"))

    def test_boundary_prices_valid(self) -> None:
        """Test that boundary values 0 and 1 are accepted."""
        snap_zero = _snapshot(yes_price=ZERO, no_price=ZERO)
        assert snap_zero.yes_price == ZERO

        one = Decimal(1)
        snap_one = _snapshot(yes_price=one, no_price=one)
        assert snap_one.yes_price == one


class TestBotConfig:
    """Tests for BotConfig frozen dataclass."""

    def test_defaults(self) -> None:
        """Test that BotConfig has sensible defaults."""
        config = BotConfig()
        assert config.poll_interval_seconds == 30  # noqa: PLR2004
        assert config.initial_capital == Decimal(1000)
        assert config.max_position_pct == Decimal("0.1")
        assert config.kelly_fraction == Decimal("0.25")
        assert config.max_history == 500  # noqa: PLR2004
        assert config.markets == ()
        assert config.series_slugs == ()

    def test_custom_values(self) -> None:
        """Test creating BotConfig with custom values."""
        config = BotConfig(
            poll_interval_seconds=60,
            initial_capital=Decimal(5000),
            markets=("cond1", "cond2"),
        )
        assert config.poll_interval_seconds == 60  # noqa: PLR2004
        assert config.initial_capital == Decimal(5000)
        assert len(config.markets) == 2  # noqa: PLR2004

    def test_series_slugs(self) -> None:
        """Test that series_slugs can be set for market rotation."""
        config = BotConfig(
            series_slugs=("btc-updown-5m", "eth-updown-5m"),
        )
        assert len(config.series_slugs) == 2  # noqa: PLR2004
        assert config.series_slugs[0] == "btc-updown-5m"


class TestPaperTrade:
    """Tests for PaperTrade frozen dataclass."""

    def test_create_paper_trade(self) -> None:
        """Test creating a paper trade with all fields."""
        trade = PaperTrade(
            condition_id=_CONDITION_ID,
            token_outcome="Yes",
            side=Side.BUY,
            quantity=Decimal(100),
            price=Decimal("0.65"),
            timestamp=_TIMESTAMP,
            reason="Z-score below threshold",
            estimated_edge=Decimal("0.05"),
        )
        assert trade.side == Side.BUY
        assert trade.token_outcome == "Yes"
        assert trade.estimated_edge == Decimal("0.05")

    def test_frozen(self) -> None:
        """Test that PaperTrade is immutable."""
        trade = PaperTrade(
            condition_id=_CONDITION_ID,
            token_outcome="Yes",
            side=Side.BUY,
            quantity=Decimal(100),
            price=Decimal("0.65"),
            timestamp=_TIMESTAMP,
            reason="test",
            estimated_edge=ZERO,
        )
        with pytest.raises(AttributeError):
            trade.price = Decimal("0.7")  # type: ignore[misc]


class TestPaperTradingResult:
    """Tests for PaperTradingResult frozen dataclass."""

    def test_create_result(self) -> None:
        """Test creating a paper trading result."""
        result = PaperTradingResult(
            strategy_name="pm_mean_reversion",
            initial_capital=Decimal(1000),
            final_capital=Decimal(1100),
            trades=(),
            snapshots_processed=50,
        )
        assert result.strategy_name == "pm_mean_reversion"
        assert result.final_capital == Decimal(1100)
        assert result.metrics == {}

    def test_result_with_metrics(self) -> None:
        """Test creating a result with custom metrics."""
        metrics = {"total_return": Decimal("0.10"), "win_rate": Decimal("0.6")}
        result = PaperTradingResult(
            strategy_name="test",
            initial_capital=Decimal(1000),
            final_capital=Decimal(1100),
            trades=(),
            snapshots_processed=10,
            metrics=metrics,
        )
        assert result.metrics["total_return"] == Decimal("0.10")
