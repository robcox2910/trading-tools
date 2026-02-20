"""Tests for Binance HTTP client."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trading_tools.clients.binance.client import BinanceClient
from trading_tools.clients.binance.exceptions import BinanceAPIError

_BINANCE_ERROR_CODE = -1121


class TestBinanceClient:
    """Test suite for Binance HTTP client."""

    @pytest.fixture
    def client(self) -> BinanceClient:
        """Create a BinanceClient instance."""
        return BinanceClient(base_url="https://api.binance.com")

    def test_client_initialization(self) -> None:
        """Test client can be initialized with defaults."""
        client = BinanceClient()
        assert client.base_url == "https://api.binance.com"

    def test_client_custom_base_url(self) -> None:
        """Test client accepts a custom base URL."""
        client = BinanceClient(base_url="https://testnet.binance.vision")
        assert client.base_url == "https://testnet.binance.vision"

    def test_trailing_slash_stripped(self) -> None:
        """Test trailing slash is stripped from base URL."""
        client = BinanceClient(base_url="https://api.binance.com/")
        assert client.base_url == "https://api.binance.com"

    @pytest.mark.asyncio
    async def test_get_request(self, client: BinanceClient) -> None:
        """Test making a successful GET request."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [["data"]]

        with patch.object(
            client._http_client, "request", new=AsyncMock(return_value=mock_response)
        ):
            result = await client.get("/api/v3/klines", params={"symbol": "BTCUSDT"})
            assert result == [["data"]]

    @pytest.mark.asyncio
    async def test_get_prepends_slash(self, client: BinanceClient) -> None:
        """Test that a missing leading slash is added to the path."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = []

        with patch.object(
            client._http_client, "request", new=AsyncMock(return_value=mock_response)
        ) as mock_request:
            await client.get("api/v3/klines")
            # httpx.AsyncClient.request receives (method, url) as positional args
            called_url = mock_request.call_args[1].get("url", mock_request.call_args[0][1])
            assert called_url == "https://api.binance.com/api/v3/klines"

    @pytest.mark.asyncio
    async def test_error_response_raises_api_error(self, client: BinanceClient) -> None:
        """Test that a Binance error response raises BinanceAPIError."""
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.json.return_value = {
            "code": _BINANCE_ERROR_CODE,
            "msg": "Invalid symbol.",
        }

        with (
            patch.object(client._http_client, "request", new=AsyncMock(return_value=mock_response)),
            pytest.raises(BinanceAPIError, match="Invalid symbol") as exc_info,
        ):
            await client.get("/api/v3/klines", params={"symbol": "BAD"})

        assert exc_info.value.code == _BINANCE_ERROR_CODE
        assert exc_info.value.msg == "Invalid symbol."

    @pytest.mark.asyncio
    async def test_error_non_json_body(self, client: BinanceClient) -> None:
        """Test error handling when response body is not JSON."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.json.side_effect = ValueError("not json")

        with (
            patch.object(client._http_client, "request", new=AsyncMock(return_value=mock_response)),
            pytest.raises(BinanceAPIError, match="HTTP 500"),
        ):
            await client.get("/api/v3/klines")

    @pytest.mark.asyncio
    async def test_context_manager(self) -> None:
        """Test client can be used as async context manager."""
        async with BinanceClient() as client:
            assert client is not None

    @pytest.mark.asyncio
    async def test_close_client(self, client: BinanceClient) -> None:
        """Test closing the client."""
        with patch.object(client._http_client, "aclose", new=AsyncMock()) as mock_close:
            await client.close()
            mock_close.assert_called_once()
