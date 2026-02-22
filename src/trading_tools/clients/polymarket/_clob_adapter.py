"""Isolated bridge to the untyped ``py-clob-client`` library.

This is the **only** module that imports from ``py_clob_client``.  All
imports carry ``# type: ignore[import-untyped]`` so the rest of the
codebase remains clean under pyright strict mode.  Functions return
primitive types (``dict``, ``str``) which the facade layer converts into
typed dataclasses.
"""

import logging
from typing import Any

from py_clob_client.client import ClobClient  # type: ignore[import-untyped]
from py_clob_client.exceptions import PolyApiException  # type: ignore[import-untyped]

from trading_tools.clients.polymarket.exceptions import PolymarketAPIError

_HTTP_INTERNAL_ERROR = 500
_HTTP_NOT_FOUND = 404

_logger = logging.getLogger(__name__)


def create_clob_client(host: str) -> ClobClient:  # type: ignore[no-any-unimported]
    """Create and return a CLOB client instance.

    Args:
        host: Base URL for the Polymarket CLOB API.

    Returns:
        Configured ``ClobClient`` ready for API calls.

    """
    return ClobClient(host)  # type: ignore[no-any-return]


def fetch_order_book(client: Any, token_id: str) -> dict[str, Any] | None:
    """Fetch the full order book for a token.

    Return ``None`` when the CLOB has no order book for the token (HTTP 404)
    so callers can return an empty book instead of crashing.

    Args:
        client: A ``ClobClient`` instance.
        token_id: CLOB token identifier.

    Returns:
        Raw order book dictionary with ``bids`` and ``asks`` keys, or ``None``
        if no order book exists.

    Raises:
        PolymarketAPIError: When the CLOB API call fails with a non-404 error.

    """
    try:
        result: dict[str, Any] = client.get_order_book(token_id)  # type: ignore[no-any-return]
    except PolyApiException as exc:
        if getattr(exc, "status_code", None) == _HTTP_NOT_FOUND:
            _logger.debug("No order book for token %s, returning None", token_id)
            return None
        raise PolymarketAPIError(
            msg=f"Failed to fetch order book for {token_id}: {exc}",
            status_code=_HTTP_INTERNAL_ERROR,
        ) from exc
    except Exception as exc:
        raise PolymarketAPIError(
            msg=f"Failed to fetch order book for {token_id}: {exc}",
            status_code=_HTTP_INTERNAL_ERROR,
        ) from exc
    else:
        return result


def fetch_price(client: Any, token_id: str, side: str) -> str | None:
    """Fetch the current price for a token on the given side.

    Return ``None`` when the CLOB has no order book for the token (HTTP 404).

    Args:
        client: A ``ClobClient`` instance.
        token_id: CLOB token identifier.
        side: Order side -- use ``"BUY"`` or ``"SELL"``.

    Returns:
        Price as a string, or ``None`` if unavailable or no order book exists.

    Raises:
        PolymarketAPIError: When the CLOB API call fails with a non-404 error.

    """
    try:
        result: str | None = client.get_price(token_id, side)
    except PolyApiException as exc:
        if getattr(exc, "status_code", None) == _HTTP_NOT_FOUND:
            _logger.debug("No order book for token %s, returning None", token_id)
            return None
        raise PolymarketAPIError(
            msg=f"Failed to fetch price for {token_id}: {exc}",
            status_code=_HTTP_INTERNAL_ERROR,
        ) from exc
    except Exception as exc:
        raise PolymarketAPIError(
            msg=f"Failed to fetch price for {token_id}: {exc}",
            status_code=_HTTP_INTERNAL_ERROR,
        ) from exc
    else:
        return result


def fetch_midpoint(client: Any, token_id: str) -> str | None:
    """Fetch the midpoint price for a token.

    Return ``None`` when the CLOB has no order book for the token (HTTP 404)
    so callers can fall back to an alternative price source.

    Args:
        client: A ``ClobClient`` instance.
        token_id: CLOB token identifier.

    Returns:
        Midpoint price as a string, or ``None`` if unavailable or no order
        book exists.

    Raises:
        PolymarketAPIError: When the CLOB API call fails with a non-404 error.

    """
    try:
        result: str | None = client.get_midpoint(token_id)
    except PolyApiException as exc:
        if getattr(exc, "status_code", None) == _HTTP_NOT_FOUND:
            _logger.debug("No order book for token %s, returning None", token_id)
            return None
        raise PolymarketAPIError(
            msg=f"Failed to fetch midpoint for {token_id}: {exc}",
            status_code=_HTTP_INTERNAL_ERROR,
        ) from exc
    except Exception as exc:
        raise PolymarketAPIError(
            msg=f"Failed to fetch midpoint for {token_id}: {exc}",
            status_code=_HTTP_INTERNAL_ERROR,
        ) from exc
    else:
        return result


def fetch_last_trade_price(client: Any, token_id: str) -> str | None:
    """Fetch the last trade price for a token.

    Return ``None`` when the CLOB has no order book for the token (HTTP 404).

    Args:
        client: A ``ClobClient`` instance.
        token_id: CLOB token identifier.

    Returns:
        Last trade price as a string, or ``None`` if unavailable or no order
        book exists.

    Raises:
        PolymarketAPIError: When the CLOB API call fails with a non-404 error.

    """
    try:
        result: str | None = client.get_last_trade_price(token_id)
    except PolyApiException as exc:
        if getattr(exc, "status_code", None) == _HTTP_NOT_FOUND:
            _logger.debug("No order book for token %s, returning None", token_id)
            return None
        raise PolymarketAPIError(
            msg=f"Failed to fetch last trade price for {token_id}: {exc}",
            status_code=_HTTP_INTERNAL_ERROR,
        ) from exc
    except Exception as exc:
        raise PolymarketAPIError(
            msg=f"Failed to fetch last trade price for {token_id}: {exc}",
            status_code=_HTTP_INTERNAL_ERROR,
        ) from exc
    else:
        return result
