"""Tests for the CLOB adapter bridge module."""

from unittest.mock import MagicMock, patch

import pytest
from py_clob_client.exceptions import PolyApiException  # type: ignore[import-untyped]

from trading_tools.clients.polymarket import _clob_adapter
from trading_tools.clients.polymarket.exceptions import PolymarketAPIError

_TOKEN_ID = "test_token_123"
_ORDER_ID = "order_abc_123"
_PRIVATE_KEY = "0xdeadbeef"
_HOST = "https://clob.polymarket.com"
_HTTP_NOT_FOUND = 404
_HTTP_SERVER_ERROR = 500


def _make_poly_api_exception(status_code: int) -> PolyApiException:
    """Create a PolyApiException with the given status code.

    Args:
        status_code: HTTP status code to set on the exception.

    Returns:
        Configured PolyApiException instance.

    """
    exc = PolyApiException(error_msg="test error")
    exc.status_code = status_code
    return exc


class TestFetchMidpoint404:
    """Test that fetch_midpoint returns None on 404."""

    def test_returns_none_on_404(self) -> None:
        """Return None when CLOB has no order book (404)."""
        client = MagicMock()
        client.get_midpoint.side_effect = _make_poly_api_exception(_HTTP_NOT_FOUND)
        result = _clob_adapter.fetch_midpoint(client, _TOKEN_ID)
        assert result is None

    def test_raises_on_500(self) -> None:
        """Raise PolymarketAPIError for non-404 CLOB errors."""
        client = MagicMock()
        client.get_midpoint.side_effect = _make_poly_api_exception(_HTTP_SERVER_ERROR)
        with pytest.raises(PolymarketAPIError, match="Failed to fetch midpoint"):
            _clob_adapter.fetch_midpoint(client, _TOKEN_ID)

    def test_raises_on_generic_exception(self) -> None:
        """Raise PolymarketAPIError for unexpected exceptions."""
        client = MagicMock()
        client.get_midpoint.side_effect = RuntimeError("connection lost")
        with pytest.raises(PolymarketAPIError, match="Failed to fetch midpoint"):
            _clob_adapter.fetch_midpoint(client, _TOKEN_ID)


class TestFetchOrderBook404:
    """Test that fetch_order_book returns None on 404."""

    def test_returns_none_on_404(self) -> None:
        """Return None when CLOB has no order book (404)."""
        client = MagicMock()
        client.get_order_book.side_effect = _make_poly_api_exception(_HTTP_NOT_FOUND)
        result = _clob_adapter.fetch_order_book(client, _TOKEN_ID)
        assert result is None

    def test_raises_on_500(self) -> None:
        """Raise PolymarketAPIError for non-404 CLOB errors."""
        client = MagicMock()
        client.get_order_book.side_effect = _make_poly_api_exception(_HTTP_SERVER_ERROR)
        with pytest.raises(PolymarketAPIError, match="Failed to fetch order book"):
            _clob_adapter.fetch_order_book(client, _TOKEN_ID)


class TestFetchPrice404:
    """Test that fetch_price returns None on 404."""

    def test_returns_none_on_404(self) -> None:
        """Return None when CLOB has no order book (404)."""
        client = MagicMock()
        client.get_price.side_effect = _make_poly_api_exception(_HTTP_NOT_FOUND)
        result = _clob_adapter.fetch_price(client, _TOKEN_ID, "BUY")
        assert result is None

    def test_raises_on_500(self) -> None:
        """Raise PolymarketAPIError for non-404 CLOB errors."""
        client = MagicMock()
        client.get_price.side_effect = _make_poly_api_exception(_HTTP_SERVER_ERROR)
        with pytest.raises(PolymarketAPIError, match="Failed to fetch price"):
            _clob_adapter.fetch_price(client, _TOKEN_ID, "BUY")


class TestFetchLastTradePrice404:
    """Test that fetch_last_trade_price returns None on 404."""

    def test_returns_none_on_404(self) -> None:
        """Return None when CLOB has no order book (404)."""
        client = MagicMock()
        client.get_last_trade_price.side_effect = _make_poly_api_exception(_HTTP_NOT_FOUND)
        result = _clob_adapter.fetch_last_trade_price(client, _TOKEN_ID)
        assert result is None

    def test_raises_on_500(self) -> None:
        """Raise PolymarketAPIError for non-404 CLOB errors."""
        client = MagicMock()
        client.get_last_trade_price.side_effect = _make_poly_api_exception(_HTTP_SERVER_ERROR)
        with pytest.raises(PolymarketAPIError, match="Failed to fetch last trade price"):
            _clob_adapter.fetch_last_trade_price(client, _TOKEN_ID)


class TestCreateAuthenticatedClobClient:
    """Test authenticated CLOB client creation."""

    def test_creates_level1_client_without_creds(self) -> None:
        """Create a Level 1 client when no API creds are provided."""
        with patch("trading_tools.clients.polymarket._clob_adapter.ClobClient") as mock_cls:
            _clob_adapter.create_authenticated_clob_client(_HOST, _PRIVATE_KEY)
        mock_cls.assert_called_once_with(_HOST, chain_id=137, key=_PRIVATE_KEY)

    def test_creates_level2_client_with_creds(self) -> None:
        """Create a Level 2 client when API creds are provided."""
        creds = ("key", "secret", "passphrase")
        with patch("trading_tools.clients.polymarket._clob_adapter.ClobClient") as mock_cls:
            _clob_adapter.create_authenticated_clob_client(_HOST, _PRIVATE_KEY, creds=creds)
        call_kwargs = mock_cls.call_args
        assert call_kwargs[1]["key"] == _PRIVATE_KEY


class TestDeriveApiCreds:
    """Test API credential derivation."""

    def test_returns_cred_tuple(self) -> None:
        """Return a tuple of (key, secret, passphrase) from derivation."""
        client = MagicMock()
        creds_obj = MagicMock()
        creds_obj.api_key = "the_key"
        creds_obj.api_secret = "the_secret"
        creds_obj.api_passphrase = "the_pass"
        client.derive_api_key.return_value = creds_obj

        result = _clob_adapter.derive_api_creds(client)
        assert result == ("the_key", "the_secret", "the_pass")

    def test_raises_on_poly_api_exception(self) -> None:
        """Raise PolymarketAPIError when derivation fails."""
        client = MagicMock()
        client.derive_api_key.side_effect = _make_poly_api_exception(_HTTP_SERVER_ERROR)
        with pytest.raises(PolymarketAPIError, match="Failed to derive"):
            _clob_adapter.derive_api_creds(client)

    def test_raises_on_generic_exception(self) -> None:
        """Raise PolymarketAPIError for unexpected errors."""
        client = MagicMock()
        client.derive_api_key.side_effect = RuntimeError("network error")
        with pytest.raises(PolymarketAPIError, match="Failed to derive"):
            _clob_adapter.derive_api_creds(client)


class TestPlaceLimitOrder:
    """Test limit order placement via the adapter."""

    def test_returns_order_result(self) -> None:
        """Return the raw API response for a successful limit order."""
        client = MagicMock()
        client.create_order.return_value = MagicMock()
        client.post_order.return_value = {"orderID": "abc", "status": "live"}

        result = _clob_adapter.place_limit_order(client, _TOKEN_ID, "BUY", 0.65, 50.0)
        assert result["orderID"] == "abc"
        client.create_order.assert_called_once()
        client.post_order.assert_called_once()

    def test_raises_on_failure(self) -> None:
        """Raise PolymarketAPIError when order placement fails."""
        client = MagicMock()
        client.create_order.side_effect = _make_poly_api_exception(_HTTP_SERVER_ERROR)
        with pytest.raises(PolymarketAPIError, match="Failed to place limit order"):
            _clob_adapter.place_limit_order(client, _TOKEN_ID, "BUY", 0.65, 50.0)


class TestPlaceMarketOrder:
    """Test market order placement via the adapter."""

    def test_returns_order_result(self) -> None:
        """Return the raw API response for a successful market order."""
        client = MagicMock()
        client.create_market_order.return_value = MagicMock()
        client.post_order.return_value = {"orderID": "xyz", "status": "matched"}

        result = _clob_adapter.place_market_order(client, _TOKEN_ID, "BUY", 100.0)
        assert result["orderID"] == "xyz"

    def test_raises_on_failure(self) -> None:
        """Raise PolymarketAPIError when market order fails."""
        client = MagicMock()
        client.create_market_order.side_effect = RuntimeError("rejected")
        with pytest.raises(PolymarketAPIError, match="Failed to place market order"):
            _clob_adapter.place_market_order(client, _TOKEN_ID, "BUY", 100.0)


class TestGetBalance:
    """Test balance retrieval via the adapter."""

    def test_returns_balance_dict(self) -> None:
        """Return balance and allowance for COLLATERAL."""
        client = MagicMock()
        client.get_balance_allowance.return_value = {"balance": "1000", "allowance": "500"}

        result = _clob_adapter.get_balance(client, "COLLATERAL")
        assert result["balance"] == "1000"
        assert result["allowance"] == "500"

    def test_raises_on_failure(self) -> None:
        """Raise PolymarketAPIError when balance query fails."""
        client = MagicMock()
        client.get_balance_allowance.side_effect = _make_poly_api_exception(_HTTP_SERVER_ERROR)
        with pytest.raises(PolymarketAPIError, match="Failed to fetch balance"):
            _clob_adapter.get_balance(client, "COLLATERAL")


class TestCancelOrder:
    """Test order cancellation via the adapter."""

    def test_returns_cancel_result(self) -> None:
        """Return the API response on successful cancellation."""
        client = MagicMock()
        client.cancel.return_value = {"status": "cancelled"}

        result = _clob_adapter.cancel_order(client, _ORDER_ID)
        assert result["status"] == "cancelled"
        client.cancel.assert_called_once_with(_ORDER_ID)

    def test_raises_on_failure(self) -> None:
        """Raise PolymarketAPIError when cancellation fails."""
        client = MagicMock()
        client.cancel.side_effect = _make_poly_api_exception(_HTTP_NOT_FOUND)
        with pytest.raises(PolymarketAPIError, match="Failed to cancel order"):
            _clob_adapter.cancel_order(client, _ORDER_ID)


class TestGetOpenOrders:
    """Test open orders retrieval via the adapter."""

    def test_returns_order_list(self) -> None:
        """Return a list of order dicts."""
        client = MagicMock()
        client.get_orders.return_value = [
            {"id": "o1", "side": "BUY", "price": "0.65"},
            {"id": "o2", "side": "SELL", "price": "0.70"},
        ]
        expected_count = 2

        result = _clob_adapter.get_open_orders(client)
        assert len(result) == expected_count
        assert result[0]["id"] == "o1"

    def test_returns_empty_for_non_list(self) -> None:
        """Return empty list when API returns a non-list response."""
        client = MagicMock()
        client.get_orders.return_value = None

        result = _clob_adapter.get_open_orders(client)
        assert result == []

    def test_raises_on_failure(self) -> None:
        """Raise PolymarketAPIError when the query fails."""
        client = MagicMock()
        client.get_orders.side_effect = _make_poly_api_exception(_HTTP_SERVER_ERROR)
        with pytest.raises(PolymarketAPIError, match="Failed to fetch open orders"):
            _clob_adapter.get_open_orders(client)
