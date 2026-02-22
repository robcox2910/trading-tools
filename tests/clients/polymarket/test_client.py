"""Tests for the Polymarket client facade."""

import json
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from trading_tools.clients.polymarket.client import PolymarketClient, _safe_decimal
from trading_tools.clients.polymarket.exceptions import PolymarketAPIError
from trading_tools.clients.polymarket.models import OrderBook

_PRICE_YES = "0.72"
_PRICE_NO = "0.28"
_MIDPOINT = "0.71"
_TOKEN_YES = "token_yes"
_TOKEN_NO = "token_no"
_BOOK_TOKEN = "token123"
_EXPECTED_BTC_MATCHES = 2
_EXPECTED_TOKEN_COUNT = 2
_EXPECTED_BOOK_LEVELS = 2
_STATUS_NOT_FOUND = 404


def _make_gamma_market(
    condition_id: str = "cond1",
    question: str = "Will Bitcoin reach $100K?",
    *,
    active: bool = True,
) -> dict[str, Any]:
    """Create a mock Gamma API market dictionary.

    Args:
        condition_id: Market condition identifier.
        question: Market question text.
        active: Whether the market is active.

    Returns:
        Dictionary matching Gamma API market response format.

    """
    return {
        "conditionId": condition_id,
        "question": question,
        "description": "Test market description",
        "outcomes": json.dumps(["Yes", "No"]),
        "outcomePrices": json.dumps([_PRICE_YES, _PRICE_NO]),
        "clobTokenIds": json.dumps([_TOKEN_YES, _TOKEN_NO]),
        "endDate": "2026-03-31",
        "volume": "50000",
        "liquidity": "10000",
        "active": active,
    }


def _make_clob_market(
    condition_id: str = "cond1",
    question: str = "Will Bitcoin reach $100K?",
    *,
    active: bool = True,
) -> dict[str, Any]:
    """Create a mock CLOB API market dictionary.

    Args:
        condition_id: Market condition identifier.
        question: Market question text.
        active: Whether the market is active.

    Returns:
        Dictionary matching CLOB ``/markets/`` endpoint response format.

    """
    return {
        "condition_id": condition_id,
        "question": question,
        "description": "Test market description",
        "tokens": [
            {"token_id": _TOKEN_YES, "outcome": "Yes", "price": float(_PRICE_YES)},
            {"token_id": _TOKEN_NO, "outcome": "No", "price": float(_PRICE_NO)},
        ],
        "end_date_iso": "2026-03-31",
        "active": active,
    }


class TestPolymarketClient:
    """Test suite for the PolymarketClient facade."""

    @pytest.fixture
    def client(self) -> PolymarketClient:
        """Create a PolymarketClient with mocked dependencies."""
        with patch("trading_tools.clients.polymarket.client._clob_adapter.create_clob_client"):
            return PolymarketClient()

    @pytest.mark.asyncio
    async def test_search_markets_filters_by_keyword(self, client: PolymarketClient) -> None:
        """Test search_markets filters results by keyword match on question."""
        gamma_markets = [
            _make_gamma_market(question="Will Bitcoin reach $100K?"),
            _make_gamma_market(condition_id="c2", question="Will ETH hit $5K?"),
            _make_gamma_market(condition_id="c3", question="Bitcoin price above $90K?"),
        ]

        with patch.object(client._gamma, "get_markets", new=AsyncMock(return_value=gamma_markets)):
            results = await client.search_markets("Bitcoin", limit=20)

        assert len(results) == _EXPECTED_BTC_MATCHES
        assert all("bitcoin" in m.question.lower() for m in results)

    @pytest.mark.asyncio
    async def test_search_markets_case_insensitive(self, client: PolymarketClient) -> None:
        """Test search_markets performs case-insensitive matching."""
        gamma_markets = [
            _make_gamma_market(question="BITCOIN to $200K?"),
        ]

        with patch.object(client._gamma, "get_markets", new=AsyncMock(return_value=gamma_markets)):
            results = await client.search_markets("bitcoin")

        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_search_markets_respects_limit(self, client: PolymarketClient) -> None:
        """Test search_markets respects the limit parameter."""
        expected_limit = 3
        gamma_markets = [
            _make_gamma_market(condition_id=f"c{i}", question=f"Bitcoin Q{i}?") for i in range(10)
        ]

        with patch.object(client._gamma, "get_markets", new=AsyncMock(return_value=gamma_markets)):
            results = await client.search_markets("Bitcoin", limit=expected_limit)

        assert len(results) == expected_limit

    @pytest.mark.asyncio
    async def test_search_markets_no_matches(self, client: PolymarketClient) -> None:
        """Test search_markets returns empty list when no matches."""
        gamma_markets = [
            _make_gamma_market(question="Will ETH hit $5K?"),
        ]

        with patch.object(client._gamma, "get_markets", new=AsyncMock(return_value=gamma_markets)):
            results = await client.search_markets("Bitcoin")

        assert results == []

    @pytest.mark.asyncio
    async def test_get_market_enriches_prices(self, client: PolymarketClient) -> None:
        """Test get_market enriches tokens with live CLOB prices."""
        raw_market = _make_clob_market()

        with (
            patch(
                "trading_tools.clients.polymarket.client._clob_adapter.fetch_market",
                return_value=raw_market,
            ),
            patch(
                "trading_tools.clients.polymarket.client._clob_adapter.fetch_midpoint",
                return_value=_MIDPOINT,
            ),
        ):
            market = await client.get_market("cond1")

        assert market.condition_id == "cond1"
        assert len(market.tokens) == _EXPECTED_TOKEN_COUNT
        # Live price should override CLOB market price
        yes_token = next(t for t in market.tokens if t.outcome == "Yes")
        assert yes_token.price == Decimal(_MIDPOINT)

    @pytest.mark.asyncio
    async def test_get_market_falls_back_to_clob_price(self, client: PolymarketClient) -> None:
        """Test get_market uses CLOB market price when midpoint returns None."""
        raw_market = _make_clob_market()

        with (
            patch(
                "trading_tools.clients.polymarket.client._clob_adapter.fetch_market",
                return_value=raw_market,
            ),
            patch(
                "trading_tools.clients.polymarket.client._clob_adapter.fetch_midpoint",
                return_value=None,
            ),
        ):
            market = await client.get_market("cond1")

        yes_token = next(t for t in market.tokens if t.outcome == "Yes")
        assert yes_token.price == Decimal(_PRICE_YES)

    @pytest.mark.asyncio
    async def test_get_order_book(self, client: PolymarketClient) -> None:
        """Test get_order_book returns a typed OrderBook."""
        raw_book = {
            "bids": [
                {"price": "0.70", "size": "100"},
                {"price": "0.69", "size": "200"},
            ],
            "asks": [
                {"price": "0.73", "size": "150"},
                {"price": "0.74", "size": "50"},
            ],
        }

        with patch(
            "trading_tools.clients.polymarket.client._clob_adapter.fetch_order_book",
            return_value=raw_book,
        ):
            book = await client.get_order_book(_BOOK_TOKEN)

        assert isinstance(book, OrderBook)
        assert book.token_id == _BOOK_TOKEN
        assert len(book.bids) == _EXPECTED_BOOK_LEVELS
        assert len(book.asks) == _EXPECTED_BOOK_LEVELS
        assert book.bids[0].price == Decimal("0.70")
        assert book.asks[0].price == Decimal("0.73")
        assert book.spread == Decimal("0.03")

    @pytest.mark.asyncio
    async def test_get_order_book_empty(self, client: PolymarketClient) -> None:
        """Test get_order_book handles empty order book."""
        with patch(
            "trading_tools.clients.polymarket.client._clob_adapter.fetch_order_book",
            return_value={"bids": [], "asks": []},
        ):
            book = await client.get_order_book(_BOOK_TOKEN)

        assert book.bids == ()
        assert book.asks == ()
        assert book.spread == Decimal(0)
        assert book.midpoint == Decimal(0)

    @pytest.mark.asyncio
    async def test_context_manager(self) -> None:
        """Test client can be used as async context manager."""
        with patch("trading_tools.clients.polymarket.client._clob_adapter.create_clob_client"):
            async with PolymarketClient() as client:
                assert client is not None

    @pytest.mark.asyncio
    async def test_get_market_propagates_error(self, client: PolymarketClient) -> None:
        """Test get_market raises PolymarketAPIError when CLOB returns None."""
        with (
            patch(
                "trading_tools.clients.polymarket.client._clob_adapter.fetch_market",
                return_value=None,
            ),
            pytest.raises(PolymarketAPIError, match="Market not found"),
        ):
            await client.get_market("bad_id")

    @pytest.mark.asyncio
    async def test_parse_market_json_string_fields(self, client: PolymarketClient) -> None:
        """Test _parse_market correctly handles JSON-encoded string fields."""
        raw = _make_gamma_market()
        market = client._parse_market(raw)

        assert len(market.tokens) == _EXPECTED_TOKEN_COUNT
        assert market.tokens[0].outcome == "Yes"
        assert market.tokens[0].price == Decimal(_PRICE_YES)
        assert market.tokens[1].outcome == "No"
        assert market.tokens[1].price == Decimal(_PRICE_NO)

    @pytest.mark.asyncio
    async def test_get_order_book_unsorted_bids_asks(self, client: PolymarketClient) -> None:
        """Test order book sorts bids descending and asks ascending."""
        raw_book = {
            "bids": [
                {"price": "0.69", "size": "200"},
                {"price": "0.70", "size": "100"},
            ],
            "asks": [
                {"price": "0.74", "size": "50"},
                {"price": "0.73", "size": "150"},
            ],
        }

        with patch(
            "trading_tools.clients.polymarket.client._clob_adapter.fetch_order_book",
            return_value=raw_book,
        ):
            book = await client.get_order_book(_BOOK_TOKEN)

        # Bids sorted descending by price
        assert book.bids[0].price == Decimal("0.70")
        assert book.bids[1].price == Decimal("0.69")
        # Asks sorted ascending by price
        assert book.asks[0].price == Decimal("0.73")
        assert book.asks[1].price == Decimal("0.74")
        assert book.spread == Decimal("0.03")

    @pytest.mark.asyncio
    async def test_get_market_zero_price_not_replaced(self, client: PolymarketClient) -> None:
        """Test that Decimal('0.00') from CLOB midpoint is used, not replaced."""
        raw_market = _make_clob_market()

        with (
            patch(
                "trading_tools.clients.polymarket.client._clob_adapter.fetch_market",
                return_value=raw_market,
            ),
            patch(
                "trading_tools.clients.polymarket.client._clob_adapter.fetch_midpoint",
                return_value="0.00",
            ),
        ):
            market = await client.get_market("cond1")

        yes_token = next(t for t in market.tokens if t.outcome == "Yes")
        assert yes_token.price == Decimal("0.00")

    @pytest.mark.asyncio
    async def test_get_market_falls_back_on_404(self, client: PolymarketClient) -> None:
        """Fall back to CLOB market price when midpoint returns 404 (None)."""
        raw_market = _make_clob_market()

        with (
            patch(
                "trading_tools.clients.polymarket.client._clob_adapter.fetch_market",
                return_value=raw_market,
            ),
            patch(
                "trading_tools.clients.polymarket.client._clob_adapter.fetch_midpoint",
                return_value=None,
            ),
        ):
            market = await client.get_market("cond1")

        yes_token = next(t for t in market.tokens if t.outcome == "Yes")
        assert yes_token.price == Decimal(_PRICE_YES)

    @pytest.mark.asyncio
    async def test_get_order_book_returns_empty_on_404(self, client: PolymarketClient) -> None:
        """Return empty OrderBook when CLOB returns 404 (no order book)."""
        with patch(
            "trading_tools.clients.polymarket.client._clob_adapter.fetch_order_book",
            return_value=None,
        ):
            book = await client.get_order_book(_BOOK_TOKEN)

        assert isinstance(book, OrderBook)
        assert book.token_id == _BOOK_TOKEN
        assert book.bids == ()
        assert book.asks == ()
        assert book.spread == Decimal(0)


class TestSafeDecimal:
    """Tests for _safe_decimal conversion."""

    def test_none_returns_zero(self) -> None:
        """Return zero for None input."""
        assert _safe_decimal(None) == Decimal(0)

    def test_empty_string_returns_zero(self) -> None:
        """Return zero for empty string input."""
        assert _safe_decimal("") == Decimal(0)

    def test_whitespace_string_returns_zero(self) -> None:
        """Return zero for whitespace-only string input."""
        assert _safe_decimal("  ") == Decimal(0)

    def test_valid_string_converts(self) -> None:
        """Convert a valid numeric string to Decimal."""
        assert _safe_decimal("1.23") == Decimal("1.23")

    def test_malformed_string_raises(self) -> None:
        """Raise PolymarketAPIError for malformed non-empty strings."""
        with pytest.raises(PolymarketAPIError, match="Cannot convert"):
            _safe_decimal("not_a_number")
