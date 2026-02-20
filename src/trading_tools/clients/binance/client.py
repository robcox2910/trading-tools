"""HTTP client for the Binance public API."""

from typing import Any

import httpx

from trading_tools.clients.binance.exceptions import BinanceAPIError

_HTTP_BAD_REQUEST = 400


class BinanceClient:
    """HTTP client for Binance public market-data endpoints.

    No authentication is required for public endpoints such as ``/api/v3/klines``.
    """

    BASE_URL = "https://api.binance.com"

    def __init__(
        self,
        base_url: str = BASE_URL,
        timeout: float = 30.0,
    ) -> None:
        """Initialize the Binance client.

        Args:
            base_url: Base URL for the Binance API.
            timeout: Request timeout in seconds.

        """
        self.base_url = base_url.rstrip("/")
        self._http_client = httpx.AsyncClient(timeout=timeout)

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """Send a GET request and return parsed JSON.

        Args:
            path: Request path relative to base_url.
            params: Query parameters.

        Returns:
            Parsed JSON response (list or dict).

        Raises:
            BinanceAPIError: When the API returns an error response.

        """
        if not path.startswith("/"):
            path = f"/{path}"

        url = f"{self.base_url}{path}"
        response = await self._http_client.request("GET", url, params=params)

        if response.status_code >= _HTTP_BAD_REQUEST:
            self._handle_error(response)

        result: Any = response.json()
        return result

    @staticmethod
    def _handle_error(response: httpx.Response) -> None:
        """Raise a BinanceAPIError from an error response.

        Args:
            response: HTTP response with a non-2xx status code.

        Raises:
            BinanceAPIError: Always raised with code and message from the response.

        """
        try:
            data = response.json()
            code: int = data.get("code", response.status_code)
            msg: str = data.get("msg", f"HTTP {response.status_code}")
        except Exception:
            code = response.status_code
            msg = f"HTTP {response.status_code}"
        raise BinanceAPIError(code=code, msg=msg)

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._http_client.aclose()

    async def __aenter__(self) -> "BinanceClient":
        """Enter async context manager."""
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Exit async context manager."""
        await self.close()
