"""Tests for the CLOB adapter bridge module."""

from unittest.mock import MagicMock

import pytest
from py_clob_client.exceptions import PolyApiException  # type: ignore[import-untyped]

from trading_tools.clients.polymarket import _clob_adapter
from trading_tools.clients.polymarket.exceptions import PolymarketAPIError

_TOKEN_ID = "test_token_123"
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
