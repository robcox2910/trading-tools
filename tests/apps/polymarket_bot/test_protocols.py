"""Tests for the PredictionMarketStrategy protocol."""

from decimal import Decimal

from trading_tools.apps.polymarket_bot.models import MarketSnapshot
from trading_tools.apps.polymarket_bot.protocols import PredictionMarketStrategy
from trading_tools.clients.polymarket.models import OrderBook
from trading_tools.core.models import ONE, ZERO, Side, Signal


class _StubStrategy:
    """Minimal strategy that satisfies the PredictionMarketStrategy protocol."""

    @property
    def name(self) -> str:
        """Return the strategy name."""
        return "stub"

    def on_snapshot(
        self,
        snapshot: MarketSnapshot,
        history: list[MarketSnapshot],  # noqa: ARG002
        related: list[MarketSnapshot] | None = None,  # noqa: ARG002
    ) -> Signal | None:
        """Return a BUY signal unconditionally."""
        return Signal(
            side=Side.BUY,
            symbol=snapshot.condition_id,
            strength=ONE,
            reason="stub signal",
        )


class _IncompleteStrategy:
    """Strategy missing the name property -- does not satisfy the protocol."""

    def on_snapshot(
        self,
        snapshot: MarketSnapshot,  # noqa: ARG002
        history: list[MarketSnapshot],  # noqa: ARG002
        related: list[MarketSnapshot] | None = None,  # noqa: ARG002
    ) -> Signal | None:
        """Return None."""
        return None


def _empty_order_book() -> OrderBook:
    """Create an empty order book for testing."""
    return OrderBook(token_id="tok1", bids=(), asks=(), spread=ZERO, midpoint=ZERO)


class TestPredictionMarketStrategy:
    """Tests for PredictionMarketStrategy protocol conformance."""

    def test_stub_satisfies_protocol(self) -> None:
        """Test that a complete implementation satisfies the protocol."""
        assert isinstance(_StubStrategy(), PredictionMarketStrategy)

    def test_incomplete_does_not_satisfy(self) -> None:
        """Test that a class missing name does not satisfy the protocol."""
        assert not isinstance(_IncompleteStrategy(), PredictionMarketStrategy)

    def test_stub_returns_signal(self) -> None:
        """Test that the stub strategy returns a valid signal."""
        strategy = _StubStrategy()
        snapshot = MarketSnapshot(
            condition_id="cond1",
            question="Test?",
            timestamp=1000,
            yes_price=Decimal("0.5"),
            no_price=Decimal("0.5"),
            order_book=_empty_order_book(),
            volume=Decimal(1000),
            liquidity=Decimal(500),
            end_date="2026-12-31",
        )
        signal = strategy.on_snapshot(snapshot, [])
        assert signal is not None
        assert signal.side == Side.BUY
        assert signal.symbol == "cond1"
