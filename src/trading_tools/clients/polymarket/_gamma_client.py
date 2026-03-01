r"""Async HTTP client for the Polymarket Gamma API.

The Gamma API (``https://gamma-api.polymarket.com``) provides market
metadata, search results, volume, and liquidity data.  This client
follows the ``BinanceClient`` pattern: async context manager with
structured error handling.

Note:
    The Gamma API returns ``outcomePrices`` and ``clobTokenIds`` as
    JSON-encoded strings (e.g. ``"[\"0.72\",\"0.28\"]"``).  Callers
    must call ``json.loads()`` on these fields before use.

"""

from typing import Any

import httpx

from trading_tools.clients.polymarket._constants import HTTP_BAD_REQUEST
from trading_tools.clients.polymarket.exceptions import PolymarketAPIError


class GammaClient:
    """Async HTTP client for Polymarket Gamma API market metadata.

    Provide methods to search and retrieve prediction market data
    including questions, outcomes, volumes, and liquidity.

    Args:
        base_url: Base URL for the Gamma API.
        timeout: Request timeout in seconds.

    """

    BASE_URL = "https://gamma-api.polymarket.com"

    def __init__(
        self,
        base_url: str = BASE_URL,
        timeout: float = 30.0,
    ) -> None:
        """Initialize the Gamma API client.

        Args:
            base_url: Base URL for the Gamma API.
            timeout: Request timeout in seconds.

        """
        self.base_url = base_url.rstrip("/")
        self._http_client = httpx.AsyncClient(timeout=timeout)

    async def get_markets(
        self,
        *,
        active: bool = True,
        closed: bool = False,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Fetch a paginated list of prediction markets.

        Args:
            active: Include only active (open) markets.
            closed: Include closed (resolved) markets.
            limit: Maximum number of markets to return.
            offset: Pagination offset.

        Returns:
            List of market dictionaries from the Gamma API.

        Raises:
            PolymarketAPIError: When the API returns an error response.

        """
        params: dict[str, str | int | bool] = {
            "limit": limit,
            "offset": offset,
            "active": active,
            "closed": closed,
        }
        return await self._get("/markets", params=params)

    async def get_market(self, condition_id: str) -> dict[str, Any]:
        """Fetch a single market by its condition ID.

        Args:
            condition_id: Unique identifier for the market condition.

        Returns:
            Market dictionary from the Gamma API.

        Raises:
            PolymarketAPIError: When the API returns an error response.

        """
        markets: list[dict[str, Any]] = await self._get(
            "/markets",
            params={"condition_id": condition_id},
        )
        if not markets:
            raise PolymarketAPIError(
                msg=f"Market not found: {condition_id}",
                status_code=404,
            )
        return markets[0]

    async def get_events(
        self,
        *,
        slug: str = "",
        active: bool = True,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Fetch events from the Gamma API, optionally filtered by slug.

        Events group related markets (e.g. a 5-minute crypto Up/Down series).
        Each event contains one or more markets with their condition IDs and
        end dates.

        Args:
            slug: Event slug to filter by (e.g. ``btc-updown-5m``).
            active: Include only active events.
            limit: Maximum number of events to return.

        Returns:
            List of event dictionaries from the Gamma API.

        Raises:
            PolymarketAPIError: When the API returns an error response.

        """
        params: dict[str, str | int | bool] = {
            "limit": limit,
            "active": active,
        }
        if slug:
            params["slug"] = slug
        return await self._get("/events", params=params)

    async def _get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Send a GET request and return parsed JSON.

        Args:
            path: Request path relative to base_url.
            params: Query parameters.

        Returns:
            Parsed JSON response.

        Raises:
            PolymarketAPIError: When the API returns an error response.

        """
        url = f"{self.base_url}{path}"
        try:
            response = await self._http_client.request("GET", url, params=params)
        except httpx.HTTPError as exc:
            raise PolymarketAPIError(
                msg=f"HTTP request failed: {exc}",
                status_code=HTTP_BAD_REQUEST,
            ) from exc

        if response.status_code >= HTTP_BAD_REQUEST:
            self._handle_error(response)

        result: Any = response.json()
        return result

    @staticmethod
    def _handle_error(response: httpx.Response) -> None:
        """Raise a PolymarketAPIError from an error response.

        Args:
            response: HTTP response with a non-2xx status code.

        Raises:
            PolymarketAPIError: Always raised with status code and message.

        """
        try:
            data = response.json()
            msg: str = data.get("message", f"HTTP {response.status_code}")
        except Exception:
            msg = f"HTTP {response.status_code}"
        raise PolymarketAPIError(msg=msg, status_code=response.status_code)

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._http_client.aclose()

    async def __aenter__(self) -> "GammaClient":
        """Enter async context manager."""
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Exit async context manager."""
        await self.close()
