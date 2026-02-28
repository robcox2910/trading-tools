"""Tests for the Polymarket client facade."""

import json
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trading_tools.clients.polymarket.client import (
    PolymarketClient,
    _resolve_timestamped_slugs,
    _safe_decimal,
)
from trading_tools.clients.polymarket.exceptions import PolymarketAPIError
from trading_tools.clients.polymarket.models import (
    Balance,
    OrderBook,
    OrderRequest,
    OrderResponse,
    RedeemablePosition,
)

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

    @pytest.mark.asyncio
    async def test_discover_series_markets(self, client: PolymarketClient) -> None:
        """Test discovering active markets from series slugs via Gamma events."""
        events = [
            {
                "slug": "btc-updown-5m",
                "markets": [
                    {
                        "conditionId": "cond_btc_1",
                        "active": True,
                        "endDate": "2026-02-22T12:05:00Z",
                    },
                    {
                        "conditionId": "cond_btc_2",
                        "active": False,
                        "endDate": "2026-02-22T12:00:00Z",
                    },
                ],
            },
        ]

        with patch.object(client._gamma, "get_events", new=AsyncMock(return_value=events)):
            results = await client.discover_series_markets(["btc-updown-5m"])

        assert len(results) == 1
        assert results[0] == ("cond_btc_1", "2026-02-22T12:05:00Z")

    @pytest.mark.asyncio
    async def test_discover_series_markets_multiple_slugs(self, client: PolymarketClient) -> None:
        """Test discovering markets from multiple series slugs."""

        async def mock_events(
            *,
            slug: str = "",
            active: bool = True,  # noqa: ARG001
            limit: int = 5,  # noqa: ARG001
        ) -> list[dict[str, Any]]:
            # Slugs will have epoch suffix due to -5m pattern
            if slug.startswith("btc-updown-5m-"):
                return [{"markets": [{"conditionId": "btc1", "active": True, "endDate": "end1"}]}]
            if slug.startswith("eth-updown-5m-"):
                return [{"markets": [{"conditionId": "eth1", "active": True, "endDate": "end2"}]}]
            return []

        with patch.object(client._gamma, "get_events", side_effect=mock_events):
            results = await client.discover_series_markets(["btc-updown-5m", "eth-updown-5m"])

        assert len(results) == _EXPECTED_TOKEN_COUNT
        cids = [cid for cid, _ in results]
        assert "btc1" in cids
        assert "eth1" in cids


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


class TestResolveTimestampedSlugs:
    """Tests for _resolve_timestamped_slugs helper."""

    def test_5m_slug_gets_epoch_suffix(self) -> None:
        """Test that slugs ending in -5m get a timestamp appended."""
        result = _resolve_timestamped_slugs(["btc-updown-5m"])
        assert len(result) == 1
        assert result[0].startswith("btc-updown-5m-")
        # Suffix should be a valid epoch
        epoch_str = result[0].split("-")[-1]
        epoch = int(epoch_str)
        five_minutes = 300
        assert epoch % five_minutes == 0

    def test_non_5m_slug_passes_through(self) -> None:
        """Test that slugs not ending in -5m pass through unchanged."""
        result = _resolve_timestamped_slugs(["some-other-event"])
        assert result == ["some-other-event"]

    def test_mixed_slugs(self) -> None:
        """Test a mix of 5m and non-5m slugs."""
        result = _resolve_timestamped_slugs(["btc-updown-5m", "custom-slug"])
        assert len(result) == _EXPECTED_TOKEN_COUNT
        assert result[0].startswith("btc-updown-5m-")
        assert result[1] == "custom-slug"


_PRIVATE_KEY = "0xdeadbeef"
_ORDER_TOKEN_ID = "token_yes"
_ORDER_SIZE = Decimal(50)
_ORDER_PRICE = Decimal("0.65")
_ZERO = Decimal(0)


class TestAuthenticatedPolymarketClient:
    """Test suite for authenticated PolymarketClient methods."""

    @pytest.fixture
    def auth_client(self) -> PolymarketClient:
        """Create an authenticated PolymarketClient with mocked adapter."""
        with patch(
            "trading_tools.clients.polymarket.client._clob_adapter.create_authenticated_clob_client"
        ):
            return PolymarketClient(private_key=_PRIVATE_KEY)

    @pytest.fixture
    def readonly_client(self) -> PolymarketClient:
        """Create a read-only PolymarketClient."""
        with patch("trading_tools.clients.polymarket.client._clob_adapter.create_clob_client"):
            return PolymarketClient()

    @pytest.mark.asyncio
    async def test_place_order_limit(self, auth_client: PolymarketClient) -> None:
        """Place a limit order and return a typed OrderResponse."""
        request = OrderRequest(
            token_id=_ORDER_TOKEN_ID,
            side="BUY",
            price=_ORDER_PRICE,
            size=_ORDER_SIZE,
            order_type="limit",
        )
        with patch(
            "trading_tools.clients.polymarket.client._clob_adapter.place_limit_order",
            return_value={"orderID": "abc", "status": "live", "filled": "0"},
        ):
            result = await auth_client.place_order(request)

        assert isinstance(result, OrderResponse)
        assert result.order_id == "abc"
        assert result.status == "live"
        assert result.token_id == _ORDER_TOKEN_ID

    @pytest.mark.asyncio
    async def test_place_order_market(self, auth_client: PolymarketClient) -> None:
        """Place a market order via the market order adapter path."""
        request = OrderRequest(
            token_id=_ORDER_TOKEN_ID,
            side="BUY",
            price=_ZERO,
            size=_ORDER_SIZE,
            order_type="market",
        )
        with patch(
            "trading_tools.clients.polymarket.client._clob_adapter.place_market_order",
            return_value={"orderID": "xyz", "status": "matched", "filled": "50"},
        ):
            result = await auth_client.place_order(request)

        assert result.order_id == "xyz"
        assert result.status == "matched"
        assert result.filled == _ORDER_SIZE

    @pytest.mark.asyncio
    async def test_place_order_requires_auth(self, readonly_client: PolymarketClient) -> None:
        """Raise PolymarketAPIError when placing order without auth."""
        request = OrderRequest(
            token_id=_ORDER_TOKEN_ID,
            side="BUY",
            price=_ORDER_PRICE,
            size=_ORDER_SIZE,
            order_type="limit",
        )
        with pytest.raises(PolymarketAPIError, match="Authentication required"):
            await readonly_client.place_order(request)

    @pytest.mark.asyncio
    async def test_get_balance(self, auth_client: PolymarketClient) -> None:
        """Fetch USDC balance and return typed Balance."""
        with patch(
            "trading_tools.clients.polymarket.client._clob_adapter.get_balance",
            return_value={"balance": "1000000000", "allowance": "500000000"},
        ):
            result = await auth_client.get_balance("COLLATERAL")

        assert isinstance(result, Balance)
        assert result.balance == Decimal(1000)
        assert result.allowance == Decimal(500)
        assert result.asset_type == "COLLATERAL"

    @pytest.mark.asyncio
    async def test_get_balance_requires_auth(self, readonly_client: PolymarketClient) -> None:
        """Raise PolymarketAPIError when checking balance without auth."""
        with pytest.raises(PolymarketAPIError, match="Authentication required"):
            await readonly_client.get_balance()

    @pytest.mark.asyncio
    async def test_cancel_order(self, auth_client: PolymarketClient) -> None:
        """Cancel an order and return the raw result."""
        with patch(
            "trading_tools.clients.polymarket.client._clob_adapter.cancel_order",
            return_value={"status": "cancelled"},
        ):
            result = await auth_client.cancel_order("order_123")

        assert result["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_order_requires_auth(self, readonly_client: PolymarketClient) -> None:
        """Raise PolymarketAPIError when cancelling without auth."""
        with pytest.raises(PolymarketAPIError, match="Authentication required"):
            await readonly_client.cancel_order("order_123")

    @pytest.mark.asyncio
    async def test_get_open_orders(self, auth_client: PolymarketClient) -> None:
        """Fetch open orders and return typed OrderResponse list."""
        raw_orders = [
            {
                "id": "o1",
                "side": "BUY",
                "price": "0.65",
                "original_size": "50",
                "size_matched": "10",
                "status": "live",
                "asset_id": "tok1",
            },
            {
                "id": "o2",
                "side": "SELL",
                "price": "0.80",
                "original_size": "30",
                "size_matched": "0",
                "status": "live",
                "asset_id": "tok2",
            },
        ]
        expected_count = 2

        with patch(
            "trading_tools.clients.polymarket.client._clob_adapter.get_open_orders",
            return_value=raw_orders,
        ):
            result = await auth_client.get_open_orders()

        assert len(result) == expected_count
        assert all(isinstance(o, OrderResponse) for o in result)
        assert result[0].order_id == "o1"
        assert result[0].filled == Decimal(10)

    @pytest.mark.asyncio
    async def test_get_open_orders_requires_auth(self, readonly_client: PolymarketClient) -> None:
        """Raise PolymarketAPIError when fetching orders without auth."""
        with pytest.raises(PolymarketAPIError, match="Authentication required"):
            await readonly_client.get_open_orders()

    @pytest.mark.asyncio
    async def test_derive_api_creds(self, auth_client: PolymarketClient) -> None:
        """Derive API creds and return a tuple."""
        with patch(
            "trading_tools.clients.polymarket.client._clob_adapter.derive_api_creds",
            return_value=("key", "secret", "pass"),
        ):
            result = await auth_client.derive_api_creds()

        assert result == ("key", "secret", "pass")

    @pytest.mark.asyncio
    async def test_derive_api_creds_requires_auth(self, readonly_client: PolymarketClient) -> None:
        """Raise PolymarketAPIError when deriving creds without auth."""
        with pytest.raises(PolymarketAPIError, match="Authentication required"):
            await readonly_client.derive_api_creds()


_FUNDER_ADDRESS = "0x21A4820a9f89cD05b715d3B10fBbBDd748d4c85D"
_REDEEMABLE_COUNT = 2


class TestGetRedeemablePositions:
    """Test suite for get_redeemable_positions Data API integration."""

    @pytest.fixture
    def client_with_funder(self) -> PolymarketClient:
        """Create a PolymarketClient with a funder address configured."""
        with patch("trading_tools.clients.polymarket.client._clob_adapter.create_clob_client"):
            return PolymarketClient(funder_address=_FUNDER_ADDRESS)

    @pytest.mark.asyncio
    async def test_returns_redeemable_positions(self, client_with_funder: PolymarketClient) -> None:
        """Return typed RedeemablePosition list from the Data API response."""
        raw_response = [
            {
                "conditionId": "0xabc123",
                "asset": "token_id_1",
                "outcome": "Down",
                "size": 6.0643,
                "title": "ETH Up or Down - Feb 24",
                "redeemable": True,
            },
            {
                "conditionId": "0xdef456",
                "asset": "token_id_2",
                "outcome": "Up",
                "size": 10.5,
                "title": "BTC Up or Down - Feb 24",
                "redeemable": True,
            },
        ]
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = raw_response

        with patch.object(
            client_with_funder._data_client,
            "get",
            new=AsyncMock(return_value=mock_response),
        ):
            result = await client_with_funder.get_redeemable_positions()

        assert len(result) == _REDEEMABLE_COUNT
        assert all(isinstance(p, RedeemablePosition) for p in result)
        assert result[0].condition_id == "0xabc123"
        assert result[0].token_id == "token_id_1"
        assert result[0].outcome == "Down"
        assert result[0].size == Decimal("6.0643")

    @pytest.mark.asyncio
    async def test_filters_zero_size_positions(self, client_with_funder: PolymarketClient) -> None:
        """Filter out positions with zero size."""
        raw_response = [
            {
                "conditionId": "0xabc123",
                "asset": "token_id_1",
                "outcome": "Down",
                "size": 0,
                "title": "Empty position",
                "redeemable": True,
            },
        ]
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = raw_response

        with patch.object(
            client_with_funder._data_client,
            "get",
            new=AsyncMock(return_value=mock_response),
        ):
            result = await client_with_funder.get_redeemable_positions()

        assert result == []

    @pytest.mark.asyncio
    async def test_raises_without_funder_address(self) -> None:
        """Raise PolymarketAPIError when funder address is not set."""
        with patch("trading_tools.clients.polymarket.client._clob_adapter.create_clob_client"):
            client = PolymarketClient()

        with pytest.raises(PolymarketAPIError, match="Funder address required"):
            await client.get_redeemable_positions()

    @pytest.mark.asyncio
    async def test_raises_on_api_error(self, client_with_funder: PolymarketClient) -> None:
        """Raise PolymarketAPIError on Data API HTTP error."""
        mock_response = MagicMock()
        mock_response.status_code = 500

        with (
            patch.object(
                client_with_funder._data_client,
                "get",
                new=AsyncMock(return_value=mock_response),
            ),
            pytest.raises(PolymarketAPIError, match="Data API error"),
        ):
            await client_with_funder.get_redeemable_positions()


_EXPECTED_PORTFOLIO_VALUE = (
    Decimal("108.86") + Decimal(10) * Decimal("0.65") + Decimal(5) * Decimal("0.80")
)
_PRIVATE_KEY_WITH_FUNDER = "0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"


class TestGetPortfolioValue:
    """Test suite for get_portfolio_value total account value calculation."""

    @pytest.fixture
    def auth_client_with_funder(self) -> PolymarketClient:
        """Create an authenticated PolymarketClient with funder address."""
        with patch(
            "trading_tools.clients.polymarket.client._clob_adapter.create_authenticated_clob_client"
        ):
            return PolymarketClient(
                private_key=_PRIVATE_KEY_WITH_FUNDER,
                funder_address=_FUNDER_ADDRESS,
            )

    @pytest.mark.asyncio
    async def test_sums_usdc_and_positions(self, auth_client_with_funder: PolymarketClient) -> None:
        """Compute total portfolio value as USDC balance + sum(size * curPrice)."""
        usdc_balance = Decimal("108.86")
        raw_positions = [
            {"size": 10.0, "curPrice": 0.65, "conditionId": "c1", "asset": "t1"},
            {"size": 5.0, "curPrice": 0.80, "conditionId": "c2", "asset": "t2"},
        ]
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = raw_positions

        with (
            patch(
                "trading_tools.clients.polymarket.client._clob_adapter.update_balance",
            ),
            patch(
                "trading_tools.clients.polymarket.client._clob_adapter.get_balance",
                return_value={"balance": str(int(usdc_balance * Decimal("1e6"))), "allowance": "0"},
            ),
            patch.object(
                auth_client_with_funder._data_client,
                "get",
                new=AsyncMock(return_value=mock_response),
            ),
        ):
            result = await auth_client_with_funder.get_portfolio_value()

        assert result == _EXPECTED_PORTFOLIO_VALUE

    @pytest.mark.asyncio
    async def test_returns_usdc_only_when_no_positions(
        self, auth_client_with_funder: PolymarketClient
    ) -> None:
        """Return USDC balance when there are no open positions."""
        usdc_balance = Decimal("100.00")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = []

        with (
            patch(
                "trading_tools.clients.polymarket.client._clob_adapter.update_balance",
            ),
            patch(
                "trading_tools.clients.polymarket.client._clob_adapter.get_balance",
                return_value={"balance": str(int(usdc_balance * Decimal("1e6"))), "allowance": "0"},
            ),
            patch.object(
                auth_client_with_funder._data_client,
                "get",
                new=AsyncMock(return_value=mock_response),
            ),
        ):
            result = await auth_client_with_funder.get_portfolio_value()

        assert result == usdc_balance

    @pytest.mark.asyncio
    async def test_requires_auth(self) -> None:
        """Raise PolymarketAPIError when called without authentication."""
        with patch("trading_tools.clients.polymarket.client._clob_adapter.create_clob_client"):
            client = PolymarketClient(funder_address=_FUNDER_ADDRESS)

        with pytest.raises(PolymarketAPIError, match="Authentication required"):
            await client.get_portfolio_value()
