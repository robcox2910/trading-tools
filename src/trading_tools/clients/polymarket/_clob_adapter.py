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
from py_clob_client.clob_types import (  # type: ignore[import-untyped]
    ApiCreds,
    AssetType,
    BalanceAllowanceParams,
    MarketOrderArgs,
    OpenOrderParams,
    OrderArgs,
    OrderType,
    PartialCreateOrderOptions,
)
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


_POLYGON_CHAIN_ID = 137


def create_authenticated_clob_client(
    host: str,
    private_key: str,
    chain_id: int = _POLYGON_CHAIN_ID,
    creds: tuple[str, str, str] | None = None,
) -> ClobClient:  # type: ignore[no-any-unimported]
    """Create an authenticated CLOB client for trading.

    When ``creds`` are provided, create a Level 2 client that can post
    orders and check balances.  Without ``creds``, create a Level 1 client
    that can derive API credentials.

    Args:
        host: Base URL for the Polymarket CLOB API.
        private_key: Polygon wallet private key (hex string with ``0x`` prefix).
        chain_id: Blockchain chain ID (default 137 for Polygon mainnet).
        creds: Optional tuple of ``(api_key, api_secret, api_passphrase)``
            for Level 2 authentication.

    Returns:
        Configured ``ClobClient`` ready for authenticated API calls.

    """
    if creds is not None:
        api_key, api_secret, api_passphrase = creds
        return ClobClient(  # type: ignore[no-any-return]
            host,
            chain_id=chain_id,
            key=private_key,
            creds=ApiCreds(
                api_key=api_key,
                api_secret=api_secret,
                api_passphrase=api_passphrase,
            ),
        )
    return ClobClient(host, chain_id=chain_id, key=private_key)  # type: ignore[no-any-return]


def derive_api_creds(client: Any) -> tuple[str, str, str]:
    """Derive API credentials from a Level 1 authenticated client.

    Perform a one-time key derivation to obtain HMAC credentials for
    Level 2 authentication.  The returned tuple can be stored and reused.

    Args:
        client: A Level 1 ``ClobClient`` instance (created with private key).

    Returns:
        Tuple of ``(api_key, api_secret, api_passphrase)``.

    Raises:
        PolymarketAPIError: When credential derivation fails.

    """
    try:
        raw: Any = client.derive_api_key()
    except PolyApiException as exc:
        raise PolymarketAPIError(
            msg=f"Failed to derive API credentials: {exc}",
            status_code=_HTTP_INTERNAL_ERROR,
        ) from exc
    except Exception as exc:
        raise PolymarketAPIError(
            msg=f"Failed to derive API credentials: {exc}",
            status_code=_HTTP_INTERNAL_ERROR,
        ) from exc
    return (str(raw.api_key), str(raw.api_secret), str(raw.api_passphrase))


def place_limit_order(
    client: Any,
    token_id: str,
    side: str,
    price: float,
    size: float,
) -> dict[str, Any]:
    """Create and post a GTC limit order on the CLOB.

    Build a signed order and submit it in a single call.  The price
    must conform to the market's tick size.

    Args:
        client: A Level 2 ``ClobClient`` instance.
        token_id: CLOB token identifier for the outcome to trade.
        side: ``"BUY"`` or ``"SELL"``.
        price: Limit price between 0 and 1.
        size: Number of shares to trade.

    Returns:
        Raw API response dictionary with order ID and status.

    Raises:
        PolymarketAPIError: When the order submission fails.

    """
    try:
        order = client.create_order(
            order_args=OrderArgs(
                token_id=token_id,
                price=price,
                size=size,
                side=side,
            ),
            options=PartialCreateOrderOptions(tick_size="0.01"),
        )
        result: dict[str, Any] = client.post_order(order, orderType=OrderType.GTC)  # type: ignore[no-any-return]
    except PolyApiException as exc:
        raise PolymarketAPIError(
            msg=f"Failed to place limit order: {exc}",
            status_code=_HTTP_INTERNAL_ERROR,
        ) from exc
    except Exception as exc:
        raise PolymarketAPIError(
            msg=f"Failed to place limit order: {exc}",
            status_code=_HTTP_INTERNAL_ERROR,
        ) from exc
    else:
        return result


def place_market_order(
    client: Any,
    token_id: str,
    side: str,
    amount: float,
) -> dict[str, Any]:
    """Create and post a FOK market order on the CLOB.

    For ``BUY`` orders, ``amount`` is the dollar amount to spend.
    For ``SELL`` orders, ``amount`` is the number of shares to sell.

    Args:
        client: A Level 2 ``ClobClient`` instance.
        token_id: CLOB token identifier for the outcome to trade.
        side: ``"BUY"`` or ``"SELL"``.
        amount: Dollar amount (buy) or share count (sell).

    Returns:
        Raw API response dictionary with order ID and status.

    Raises:
        PolymarketAPIError: When the order submission fails.

    """
    try:
        order = client.create_market_order(
            order_args=MarketOrderArgs(
                token_id=token_id,
                amount=amount,
                side=side,
            ),
            options=PartialCreateOrderOptions(tick_size="0.01"),
        )
        result: dict[str, Any] = client.post_order(order, orderType=OrderType.FOK)  # type: ignore[no-any-return]
    except PolyApiException as exc:
        raise PolymarketAPIError(
            msg=f"Failed to place market order: {exc}",
            status_code=_HTTP_INTERNAL_ERROR,
        ) from exc
    except Exception as exc:
        raise PolymarketAPIError(
            msg=f"Failed to place market order: {exc}",
            status_code=_HTTP_INTERNAL_ERROR,
        ) from exc
    else:
        return result


def get_balance(client: Any, asset_type: str = "COLLATERAL") -> dict[str, Any]:
    """Fetch balance and allowance for an asset type.

    Args:
        client: A Level 2 ``ClobClient`` instance.
        asset_type: ``"COLLATERAL"`` for USDC or ``"CONDITIONAL"`` for tokens.

    Returns:
        Dictionary with ``balance`` and ``allowance`` string values.

    Raises:
        PolymarketAPIError: When the balance query fails.

    """
    resolved_type: str = (
        AssetType.COLLATERAL if asset_type == "COLLATERAL" else AssetType.CONDITIONAL
    )
    try:
        result: dict[str, Any] = client.get_balance_allowance(  # type: ignore[no-any-return]
            params=BalanceAllowanceParams(asset_type=resolved_type),  # type: ignore[reportArgumentType]
        )
    except PolyApiException as exc:
        raise PolymarketAPIError(
            msg=f"Failed to fetch balance: {exc}",
            status_code=_HTTP_INTERNAL_ERROR,
        ) from exc
    except Exception as exc:
        raise PolymarketAPIError(
            msg=f"Failed to fetch balance: {exc}",
            status_code=_HTTP_INTERNAL_ERROR,
        ) from exc
    else:
        return result


def cancel_order(client: Any, order_id: str) -> dict[str, Any]:
    """Cancel an open order by its ID.

    Args:
        client: A Level 2 ``ClobClient`` instance.
        order_id: Identifier of the order to cancel.

    Returns:
        Raw API response confirming the cancellation.

    Raises:
        PolymarketAPIError: When the cancellation fails.

    """
    try:
        result: dict[str, Any] = client.cancel(order_id)  # type: ignore[no-any-return]
    except PolyApiException as exc:
        raise PolymarketAPIError(
            msg=f"Failed to cancel order {order_id}: {exc}",
            status_code=_HTTP_INTERNAL_ERROR,
        ) from exc
    except Exception as exc:
        raise PolymarketAPIError(
            msg=f"Failed to cancel order {order_id}: {exc}",
            status_code=_HTTP_INTERNAL_ERROR,
        ) from exc
    else:
        return result


def get_open_orders(client: Any) -> list[dict[str, Any]]:
    """Fetch all open orders for the authenticated user.

    Args:
        client: A Level 2 ``ClobClient`` instance.

    Returns:
        List of open order dictionaries.

    Raises:
        PolymarketAPIError: When the query fails.

    """
    try:
        result: Any = client.get_orders(
            params=OpenOrderParams(),
        )
    except PolyApiException as exc:
        raise PolymarketAPIError(
            msg=f"Failed to fetch open orders: {exc}",
            status_code=_HTTP_INTERNAL_ERROR,
        ) from exc
    except Exception as exc:
        raise PolymarketAPIError(
            msg=f"Failed to fetch open orders: {exc}",
            status_code=_HTTP_INTERNAL_ERROR,
        ) from exc
    else:
        if isinstance(result, list):
            return cast("list[dict[str, Any]]", result)
        return []
