"""Isolated bridge to the untyped ``py-clob-client`` library.

This is the **only** module that imports from ``py_clob_client``.  All
imports carry ``# type: ignore[import-untyped]`` so the rest of the
codebase remains clean under pyright strict mode.  Functions return
primitive types (``dict``, ``str``) which the facade layer converts into
typed dataclasses.
"""

import logging
from typing import Any, cast

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
        raw: Any = client.get_order_book(token_id)
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
        return _normalize_order_book(raw)


def _normalize_order_book(raw: Any) -> dict[str, Any]:
    """Normalize an order book response to a plain dict.

    The CLOB client may return an ``OrderBookSummary`` dataclass or a plain
    dict depending on the version. Normalize to ``{"bids": [...], "asks": [...]}``.

    Args:
        raw: Raw response from the CLOB client's ``get_order_book()``.

    Returns:
        Dictionary with ``bids`` and ``asks`` lists of price/size dicts.

    """
    if isinstance(raw, dict):
        return cast("dict[str, Any]", raw)
    bid_levels: list[Any] = getattr(raw, "bids", [])
    ask_levels: list[Any] = getattr(raw, "asks", [])
    bids: list[dict[str, str]] = [{"price": str(b.price), "size": str(b.size)} for b in bid_levels]
    asks: list[dict[str, str]] = [{"price": str(a.price), "size": str(a.size)} for a in ask_levels]
    return {"bids": bids, "asks": asks}


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
        raw: Any = client.get_midpoint(token_id)
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
        return _extract_midpoint(raw)


def _extract_midpoint(raw: Any) -> str | None:
    """Extract the midpoint value from a CLOB API response.

    The CLOB client returns ``{'mid': '0.495'}`` dict; extract the value string.

    Args:
        raw: Raw response from the CLOB client's ``get_midpoint()``.

    Returns:
        Midpoint price as a string, or ``None`` if not available.

    """
    if isinstance(raw, dict):
        mid_dict = cast("dict[str, Any]", raw)
        return str(mid_dict.get("mid", ""))
    return str(raw) if raw is not None else None


def fetch_market(client: Any, condition_id: str) -> dict[str, Any] | None:
    """Fetch market metadata from the CLOB API.

    Args:
        client: A ``ClobClient`` instance.
        condition_id: Market condition identifier (hex string).

    Returns:
        Raw market dictionary with tokens, question, etc., or ``None``
        if the market is not found.

    Raises:
        PolymarketAPIError: When the CLOB API call fails with a non-404 error.

    """
    try:
        result: dict[str, Any] = client.get_market(condition_id)  # type: ignore[no-any-return]
    except PolyApiException as exc:
        if getattr(exc, "status_code", None) == _HTTP_NOT_FOUND:
            _logger.debug("Market %s not found on CLOB", condition_id)
            return None
        raise PolymarketAPIError(
            msg=f"Failed to fetch market {condition_id}: {exc}",
            status_code=_HTTP_INTERNAL_ERROR,
        ) from exc
    except Exception as exc:
        raise PolymarketAPIError(
            msg=f"Failed to fetch market {condition_id}: {exc}",
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
