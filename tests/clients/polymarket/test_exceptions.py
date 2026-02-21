"""Tests for Polymarket exception hierarchy."""

from trading_tools.clients.polymarket.exceptions import (
    PolymarketAPIError,
    PolymarketError,
)

_STATUS_NOT_FOUND = 404
_STATUS_SERVER_ERROR = 500


class TestPolymarketError:
    """Test suite for PolymarketError base exception."""

    def test_is_exception(self) -> None:
        """Test PolymarketError inherits from Exception."""
        assert issubclass(PolymarketError, Exception)

    def test_can_be_raised(self) -> None:
        """Test PolymarketError can be raised and caught."""
        error = PolymarketError("test error")
        assert str(error) == "test error"


class TestPolymarketAPIError:
    """Test suite for PolymarketAPIError."""

    def test_inherits_from_base(self) -> None:
        """Test PolymarketAPIError inherits from PolymarketError."""
        assert issubclass(PolymarketAPIError, PolymarketError)

    def test_attributes(self) -> None:
        """Test PolymarketAPIError stores msg and status_code."""
        error = PolymarketAPIError(msg="Not found", status_code=_STATUS_NOT_FOUND)
        assert error.msg == "Not found"
        assert error.status_code == _STATUS_NOT_FOUND

    def test_string_representation(self) -> None:
        """Test PolymarketAPIError formats as '[status_code] msg'."""
        error = PolymarketAPIError(msg="Server error", status_code=_STATUS_SERVER_ERROR)
        assert str(error) == "[500] Server error"

    def test_caught_as_base(self) -> None:
        """Test PolymarketAPIError can be caught as PolymarketError."""
        error = PolymarketAPIError(msg="fail", status_code=_STATUS_SERVER_ERROR)
        assert isinstance(error, PolymarketError)
