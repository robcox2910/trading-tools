"""Tests for Revolut X HTTP client."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from trading_tools.clients.revolut_x.client import RevolutXClient
from trading_tools.clients.revolut_x.exceptions import (
    RevolutXAPIError,
    RevolutXAuthenticationError,
    RevolutXRateLimitError,
)


class TestRevolutXClient:
    """Test suite for Revolut X HTTP client."""

    @pytest.fixture
    def private_key(self) -> Ed25519PrivateKey:
        """Generate a test Ed25519 private key."""
        return Ed25519PrivateKey.generate()

    @pytest.fixture
    def api_key(self) -> str:
        """Return a test API key."""
        return "a" * 64

    @pytest.fixture
    def client(self, private_key: Ed25519PrivateKey, api_key: str) -> RevolutXClient:
        """Create a RevolutXClient instance."""
        return RevolutXClient(
            api_key=api_key,
            private_key=private_key,
            base_url="https://api.revolut.com/api/1.0",
        )

    def test_client_initialization(self, private_key: Ed25519PrivateKey, api_key: str) -> None:
        """Test client can be initialized."""
        client = RevolutXClient(
            api_key=api_key,
            private_key=private_key,
            base_url="https://test.com",
        )
        assert client.api_key == api_key
        assert client.base_url == "https://test.com"

    def test_client_initialization_from_config(self, tmp_path: Path) -> None:
        """Test client can be initialized from config."""
        # Create a test key file
        key = Ed25519PrivateKey.generate()
        from cryptography.hazmat.primitives import serialization

        pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        key_file = tmp_path / "test_key.pem"
        key_file.write_bytes(pem)

        # Create config
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config_file = config_dir / "settings.yaml"
        config_file.write_text(f"""
revolut_x:
  api_key: test_key_123
  private_key_path: {key_file}
  base_url: https://test.revolut.com
""")

        with patch("trading_tools.clients.revolut_x.client.config") as mock_config:
            mock_config.get.side_effect = lambda k, default=None: {
                "revolut_x.api_key": "test_key_123",
                "revolut_x.base_url": "https://test.revolut.com",
                "revolut_x.private_key_path": str(key_file),
            }.get(k, default)
            mock_config.get_private_key.return_value = pem

            client = RevolutXClient.from_config()
            assert client.api_key == "test_key_123"
            assert client.base_url == "https://test.revolut.com"

    @pytest.mark.asyncio
    async def test_get_request(self, client: RevolutXClient) -> None:
        """Test making a GET request."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": "test"}

        with patch.object(client, "_request", new=AsyncMock(return_value=mock_response)):
            response = await client.get("/test/endpoint")
            assert response == {"data": "test"}

    @pytest.mark.asyncio
    async def test_post_request(self, client: RevolutXClient) -> None:
        """Test making a POST request."""
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"created": True}

        with patch.object(client, "_request", new=AsyncMock(return_value=mock_response)):
            response = await client.post("/test/endpoint", data={"key": "value"})
            assert response == {"created": True}

    @pytest.mark.asyncio
    async def test_request_with_authentication_headers(self, client: RevolutXClient) -> None:
        """Test that requests include proper authentication headers."""
        with patch("httpx.AsyncClient.request", new=AsyncMock()) as mock_request:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {}
            mock_request.return_value = mock_response

            await client.get("/test")

            # Check that authentication headers were added
            call_kwargs = mock_request.call_args.kwargs
            headers = call_kwargs["headers"]
            assert "X-Revx-API-Key" in headers
            assert "X-Revx-Timestamp" in headers
            assert "X-Revx-Signature" in headers
            assert headers["X-Revx-API-Key"] == client.api_key

    @pytest.mark.asyncio
    async def test_authentication_error_handling(self, client: RevolutXClient) -> None:
        """Test handling of authentication errors."""
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.json.return_value = {"error": "Unauthorized"}

        with (
            patch.object(client._http_client, "request", new=AsyncMock(return_value=mock_response)),
            pytest.raises(RevolutXAuthenticationError, match="Unauthorized"),
        ):
            await client.get("/test")

    @pytest.mark.asyncio
    async def test_rate_limit_error_handling(self, client: RevolutXClient) -> None:
        """Test handling of rate limit errors."""
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.json.return_value = {"error": "Rate limit exceeded"}

        with (
            patch.object(client._http_client, "request", new=AsyncMock(return_value=mock_response)),
            pytest.raises(RevolutXRateLimitError, match="Rate limit exceeded"),
        ):
            await client.get("/test")

    @pytest.mark.asyncio
    async def test_generic_api_error_handling(self, client: RevolutXClient) -> None:
        """Test handling of generic API errors."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.json.return_value = {"error": "Internal server error"}

        with (
            patch.object(client._http_client, "request", new=AsyncMock(return_value=mock_response)),
            pytest.raises(RevolutXAPIError, match="Internal server error"),
        ):
            await client.get("/test")

    @pytest.mark.asyncio
    async def test_request_with_query_params(self, client: RevolutXClient) -> None:
        """Test request with query parameters."""
        with patch("httpx.AsyncClient.request", new=AsyncMock()) as mock_request:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {}
            mock_request.return_value = mock_response

            await client.get("/test", params={"key1": "value1", "key2": "value2"})

            # Check that query params were included
            call_kwargs = mock_request.call_args.kwargs
            assert "params" in call_kwargs
            assert call_kwargs["params"] == {"key1": "value1", "key2": "value2"}

    @pytest.mark.asyncio
    async def test_context_manager(self, private_key: Ed25519PrivateKey, api_key: str) -> None:
        """Test client can be used as async context manager."""
        async with RevolutXClient(
            api_key=api_key,
            private_key=private_key,
        ) as client:
            assert client is not None

    @pytest.mark.asyncio
    async def test_close_client(self, client: RevolutXClient) -> None:
        """Test closing the client."""
        with patch.object(client._http_client, "aclose", new=AsyncMock()) as mock_close:
            await client.close()
            mock_close.assert_called_once()
