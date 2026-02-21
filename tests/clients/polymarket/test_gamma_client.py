"""Tests for the Polymarket Gamma API client."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trading_tools.clients.polymarket._gamma_client import GammaClient
from trading_tools.clients.polymarket.exceptions import PolymarketAPIError

_STATUS_OK = 200
_STATUS_NOT_FOUND = 404
_STATUS_SERVER_ERROR = 500
_EXPECTED_MARKET_COUNT = 2


class TestGammaClient:
    """Test suite for GammaClient."""

    @pytest.fixture
    def client(self) -> GammaClient:
        """Create a GammaClient instance for testing."""
        return GammaClient(base_url="https://gamma-api.polymarket.com")

    def test_initialization(self) -> None:
        """Test client initializes with default base URL."""
        client = GammaClient()
        assert client.base_url == "https://gamma-api.polymarket.com"

    def test_custom_base_url(self) -> None:
        """Test client accepts a custom base URL."""
        client = GammaClient(base_url="https://custom.api.com")
        assert client.base_url == "https://custom.api.com"

    def test_trailing_slash_stripped(self) -> None:
        """Test trailing slash is stripped from base URL."""
        client = GammaClient(base_url="https://gamma-api.polymarket.com/")
        assert client.base_url == "https://gamma-api.polymarket.com"

    @pytest.mark.asyncio
    async def test_get_markets(self, client: GammaClient) -> None:
        """Test fetching a list of markets."""
        mock_response = MagicMock()
        mock_response.status_code = _STATUS_OK
        mock_response.json.return_value = [
            {"conditionId": "c1", "question": "Will BTC hit $100K?"},
            {"conditionId": "c2", "question": "Will ETH hit $5K?"},
        ]

        with patch.object(
            client._http_client, "request", new=AsyncMock(return_value=mock_response)
        ):
            result = await client.get_markets(active=True, limit=10)

        assert len(result) == _EXPECTED_MARKET_COUNT
        assert result[0]["conditionId"] == "c1"

    @pytest.mark.asyncio
    async def test_get_market_found(self, client: GammaClient) -> None:
        """Test fetching a single market by condition ID."""
        mock_response = MagicMock()
        mock_response.status_code = _STATUS_OK
        mock_response.json.return_value = [
            {"conditionId": "c1", "question": "Will BTC hit $100K?"},
        ]

        with patch.object(
            client._http_client, "request", new=AsyncMock(return_value=mock_response)
        ):
            result = await client.get_market("c1")

        assert result["conditionId"] == "c1"

    @pytest.mark.asyncio
    async def test_get_market_not_found(self, client: GammaClient) -> None:
        """Test fetching a non-existent market raises PolymarketAPIError."""
        mock_response = MagicMock()
        mock_response.status_code = _STATUS_OK
        mock_response.json.return_value = []

        with (
            patch.object(client._http_client, "request", new=AsyncMock(return_value=mock_response)),
            pytest.raises(PolymarketAPIError, match="Market not found"),
        ):
            await client.get_market("nonexistent")

    @pytest.mark.asyncio
    async def test_error_response(self, client: GammaClient) -> None:
        """Test that HTTP error responses raise PolymarketAPIError."""
        mock_response = MagicMock()
        mock_response.status_code = _STATUS_SERVER_ERROR
        mock_response.json.return_value = {"message": "Internal error"}

        with (
            patch.object(client._http_client, "request", new=AsyncMock(return_value=mock_response)),
            pytest.raises(PolymarketAPIError, match="Internal error"),
        ):
            await client.get_markets()

    @pytest.mark.asyncio
    async def test_error_non_json_body(self, client: GammaClient) -> None:
        """Test error handling when response body is not JSON."""
        mock_response = MagicMock()
        mock_response.status_code = _STATUS_SERVER_ERROR
        mock_response.json.side_effect = ValueError("not json")

        with (
            patch.object(client._http_client, "request", new=AsyncMock(return_value=mock_response)),
            pytest.raises(PolymarketAPIError, match="HTTP 500"),
        ):
            await client.get_markets()

    @pytest.mark.asyncio
    async def test_context_manager(self) -> None:
        """Test client can be used as async context manager."""
        async with GammaClient() as client:
            assert client is not None

    @pytest.mark.asyncio
    async def test_close(self, client: GammaClient) -> None:
        """Test closing the client."""
        with patch.object(client._http_client, "aclose", new=AsyncMock()) as mock_close:
            await client.close()
            mock_close.assert_called_once()
