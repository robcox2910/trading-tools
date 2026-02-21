"""Tests for Polymarket typed data models."""

from decimal import Decimal

import pytest

from trading_tools.clients.polymarket.models import (
    Market,
    MarketToken,
    OrderBook,
    OrderLevel,
)

_PRICE = Decimal("0.72")
_SIZE = Decimal("100.5")
_VOLUME = Decimal(50000)
_LIQUIDITY = Decimal(10000)
_TOKEN_ID = "tok123"
_TOKEN_ID_ALT = "tok456"
_EXPECTED_TOKEN_COUNT = 2


class TestOrderLevel:
    """Test suite for OrderLevel dataclass."""

    def test_construction(self) -> None:
        """Test OrderLevel can be created with price and size."""
        level = OrderLevel(price=_PRICE, size=_SIZE)
        assert level.price == _PRICE
        assert level.size == _SIZE

    def test_frozen(self) -> None:
        """Test OrderLevel is immutable."""
        level = OrderLevel(price=_PRICE, size=_SIZE)
        with pytest.raises(AttributeError):
            level.price = Decimal("0.5")  # type: ignore[misc]


class TestOrderBook:
    """Test suite for OrderBook dataclass."""

    def test_construction(self) -> None:
        """Test OrderBook can be created with all fields."""
        bid = OrderLevel(price=Decimal("0.70"), size=_SIZE)
        ask = OrderLevel(price=_PRICE, size=_SIZE)
        book = OrderBook(
            token_id=_TOKEN_ID,
            bids=(bid,),
            asks=(ask,),
            spread=Decimal("0.02"),
            midpoint=Decimal("0.71"),
        )
        assert book.token_id == _TOKEN_ID
        assert len(book.bids) == 1
        assert len(book.asks) == 1
        assert book.spread == Decimal("0.02")
        assert book.midpoint == Decimal("0.71")

    def test_frozen(self) -> None:
        """Test OrderBook is immutable."""
        book = OrderBook(
            token_id=_TOKEN_ID,
            bids=(),
            asks=(),
            spread=Decimal(0),
            midpoint=Decimal(0),
        )
        with pytest.raises(AttributeError):
            book.token_id = "new"  # type: ignore[misc]


class TestMarketToken:
    """Test suite for MarketToken dataclass."""

    def test_construction(self) -> None:
        """Test MarketToken can be created with all fields."""
        token = MarketToken(token_id=_TOKEN_ID_ALT, outcome="Yes", price=_PRICE)
        assert token.token_id == _TOKEN_ID_ALT
        assert token.outcome == "Yes"
        assert token.price == _PRICE

    def test_frozen(self) -> None:
        """Test MarketToken is immutable."""
        token = MarketToken(token_id=_TOKEN_ID_ALT, outcome="Yes", price=_PRICE)
        with pytest.raises(AttributeError):
            token.outcome = "No"  # type: ignore[misc]


class TestMarket:
    """Test suite for Market dataclass."""

    def test_construction(self) -> None:
        """Test Market can be created with all fields."""
        yes_token = MarketToken(token_id="t1", outcome="Yes", price=_PRICE)
        no_token = MarketToken(token_id="t2", outcome="No", price=Decimal("0.28"))
        market = Market(
            condition_id="cond123",
            question="Will BTC reach $100K?",
            description="Resolves YES if BTC >= $100K before expiry.",
            tokens=(yes_token, no_token),
            end_date="2026-03-31",
            volume=_VOLUME,
            liquidity=_LIQUIDITY,
            active=True,
        )
        assert market.condition_id == "cond123"
        assert market.question == "Will BTC reach $100K?"
        assert len(market.tokens) == _EXPECTED_TOKEN_COUNT
        assert market.active is True

    def test_frozen(self) -> None:
        """Test Market is immutable."""
        market = Market(
            condition_id="c1",
            question="Q?",
            description="D",
            tokens=(),
            end_date="2026-01-01",
            volume=Decimal(0),
            liquidity=Decimal(0),
            active=False,
        )
        with pytest.raises(AttributeError):
            market.active = True  # type: ignore[misc]
