"""HTTP client for Revolut X API."""

import json
import time
from typing import Any
from urllib.parse import quote, urlparse

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from trading_tools.clients.revolut_x.auth.signer import Ed25519Signer
from trading_tools.clients.revolut_x.exceptions import (
    RevolutXAPIError,
    RevolutXAuthenticationError,
    RevolutXNotFoundError,
    RevolutXRateLimitError,
    RevolutXValidationError,
)
from trading_tools.core.config import config

_HTTP_BAD_REQUEST = 400
_HTTP_UNAUTHORIZED = 401
_HTTP_NOT_FOUND = 404
_HTTP_TOO_MANY_REQUESTS = 429


class RevolutXClient:
    """HTTP client for Revolut X cryptocurrency API.

    This client handles authentication, request signing, and error handling
    for all API requests to the Revolut X platform.
    """

    def __init__(
        self,
        api_key: str,
        private_key: Ed25519PrivateKey,
        base_url: str = "https://revx.revolut.com/api/1.0",
        timeout: float = 30.0,
    ) -> None:
        """Initialize the Revolut X client.

        Args:
            api_key: Revolut X API key (64-character string).
            private_key: Ed25519 private key for signing requests.
            base_url: Base URL for the API.
            timeout: Request timeout in seconds.

        """
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._base_path = urlparse(self.base_url).path
        self.timeout = timeout
        self.signer = Ed25519Signer(private_key)
        self._http_client = httpx.AsyncClient(timeout=timeout)

    @classmethod
    def from_config(cls) -> "RevolutXClient":
        """Create client from configuration.

        Returns:
            Configured RevolutXClient instance.

        Raises:
            ValueError: If configuration is missing required values.

        """
        api_key = config.get("revolut_x.api_key")
        if not api_key:
            raise ValueError("revolut_x.api_key not configured")

        base_url = config.get("revolut_x.base_url", "https://revx.revolut.com/api/1.0")

        # Load private key
        private_key = Ed25519Signer.load_private_key_from_file(
            config.get("revolut_x.private_key_path")
        )

        return cls(
            api_key=api_key,
            private_key=private_key,
            base_url=base_url,
        )

    def _generate_auth_headers(
        self,
        method: str,
        path: str,
        query: str = "",
        body: str = "",
    ) -> dict[str, str]:
        """Generate authentication headers for a request.

        Args:
            method: HTTP method (GET, POST, etc.).
            path: Request path.
            query: URL query string (without leading ?).
            body: Request body as JSON string.

        Returns:
            Dictionary of authentication headers.

        """
        timestamp = str(int(time.time() * 1000))
        signature = self.signer.generate_signature(
            timestamp=timestamp,
            method=method.upper(),
            path=path,
            query=query,
            body=body,
        )

        return {
            "X-Revx-API-Key": self.api_key,
            "X-Revx-Timestamp": timestamp,
            "X-Revx-Signature": signature,
        }

    def _build_query_string(self, params: dict[str, Any] | None) -> str:
        """Build query string from parameters.

        Args:
            params: Query parameters.

        Returns:
            Query string (without leading ?).

        """
        if not params:
            return ""

        # Sort parameters for consistent signature generation
        sorted_params = sorted(params.items())
        return "&".join(f"{quote(str(k))}={quote(str(v))}" for k, v in sorted_params)

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Make an authenticated API request.

        Args:
            method: HTTP method.
            path: Request path (relative to base_url).
            params: Query parameters.
            data: Request body data.

        Returns:
            HTTP response.

        Raises:
            RevolutXAuthenticationError: For 401 errors.
            RevolutXValidationError: For 400 errors.
            RevolutXNotFoundError: For 404 errors.
            RevolutXRateLimitError: For 429 errors.
            RevolutXAPIError: For other API errors.

        """
        # Ensure path starts with /
        if not path.startswith("/"):
            path = f"/{path}"

        url = f"{self.base_url}{path}"

        # Build query string for signature
        query_string = self._build_query_string(params)

        # Serialize body for signature
        body_str = ""
        if data:
            body_str = json.dumps(data, separators=(",", ":"))  # Minified JSON

        # Generate authentication headers (signing path must start from /api)
        signing_path = f"{self._base_path}{path}"
        auth_headers = self._generate_auth_headers(
            method=method,
            path=signing_path,
            query=query_string,
            body=body_str,
        )

        # Prepare headers
        headers = {
            "Content-Type": "application/json",
            **auth_headers,
        }

        # Make request — send body as pre-serialized content so the
        # bytes on the wire match what was signed (minified JSON).
        response = await self._http_client.request(
            method=method,
            url=url,
            headers=headers,
            params=params,
            content=body_str.encode() if body_str else None,
        )

        # Handle errors
        if response.status_code >= _HTTP_BAD_REQUEST:
            self._handle_error(response)

        return response

    def _handle_error(self, response: httpx.Response) -> None:
        """Handle API error responses.

        Args:
            response: HTTP response with error.

        Raises:
            RevolutXAuthenticationError: For 401 errors.
            RevolutXValidationError: For 400 errors.
            RevolutXNotFoundError: For 404 errors.
            RevolutXRateLimitError: For 429 errors.
            RevolutXAPIError: For other errors.

        """
        try:
            error_data = response.json()
            message = error_data.get("error", "Unknown error")
        except Exception:
            message = f"HTTP {response.status_code}"

        if response.status_code == _HTTP_UNAUTHORIZED:
            raise RevolutXAuthenticationError(message, response.status_code)
        if response.status_code == _HTTP_BAD_REQUEST:
            raise RevolutXValidationError(message, response.status_code)
        if response.status_code == _HTTP_NOT_FOUND:
            raise RevolutXNotFoundError(message, response.status_code)
        if response.status_code == _HTTP_TOO_MANY_REQUESTS:
            raise RevolutXRateLimitError(message, response.status_code)

        raise RevolutXAPIError(message, response.status_code)

    async def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make a GET request.

        Args:
            path: Request path.
            params: Query parameters.

        Returns:
            Response JSON as dictionary.

        """
        response = await self._request("GET", path, params=params)
        result: dict[str, Any] = response.json()
        return result

    async def post(
        self,
        path: str,
        data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make a POST request.

        Args:
            path: Request path.
            data: Request body data.
            params: Query parameters.

        Returns:
            Response JSON as dictionary.

        """
        response = await self._request("POST", path, params=params, data=data)
        result: dict[str, Any] = response.json()
        return result

    async def put(
        self,
        path: str,
        data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make a PUT request.

        Args:
            path: Request path.
            data: Request body data.
            params: Query parameters.

        Returns:
            Response JSON as dictionary.

        """
        response = await self._request("PUT", path, params=params, data=data)
        result: dict[str, Any] = response.json()
        return result

    async def delete(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make a DELETE request.

        Args:
            path: Request path.
            params: Query parameters.

        Returns:
            Response JSON as dictionary.

        """
        response = await self._request("DELETE", path, params=params)
        result: dict[str, Any] = response.json()
        return result

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._http_client.aclose()

    async def __aenter__(self) -> "RevolutXClient":
        """Enter async context manager."""
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Exit async context manager."""
        await self.close()
