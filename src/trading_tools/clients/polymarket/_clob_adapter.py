"""Isolated bridge to the untyped ``py-clob-client`` library.

This is the **only** module that imports from ``py_clob_client``.  All
imports carry ``# type: ignore[import-untyped]`` so the rest of the
codebase remains clean under pyright strict mode.  Functions return
primitive types (``dict``, ``str``) which the facade layer converts into
typed dataclasses.
"""

import logging
from typing import Any, cast

from eth_account import Account  # type: ignore[import-untyped]
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
_POLYGON_PROXY_WALLET = 1

_logger = logging.getLogger(__name__)

_R = Any  # Generic return alias for _safe_clob_call


def _safe_clob_call(
    action: str,
    fn: Any,
    *args: Any,
    allow_404: bool = False,
) -> Any:
    """Execute a CLOB API call with standardised error handling.

    Wrap a synchronous ``py-clob-client`` call in a try/except that
    converts ``PolyApiException`` and unexpected errors into
    ``PolymarketAPIError``.  Optionally return ``None`` for HTTP 404.

    Args:
        action: Human-readable description for error messages (e.g.
            ``"fetch midpoint for <token>"``).
        fn: The callable to invoke.
        *args: Positional arguments forwarded to *fn*.
        allow_404: When ``True``, return ``None`` instead of raising
            on HTTP 404.

    Returns:
        The raw result from *fn*, or ``None`` when *allow_404* is set
        and the API returns 404.

    Raises:
        PolymarketAPIError: When the call fails and the error is not
            a suppressed 404.

    """
    try:
        return fn(*args)
    except PolyApiException as exc:
        if allow_404 and getattr(exc, "status_code", None) == _HTTP_NOT_FOUND:
            _logger.debug("404 for %s, returning None", action)
            return None
        raise PolymarketAPIError(
            msg=f"Failed to {action}: {exc}",
            status_code=_HTTP_INTERNAL_ERROR,
        ) from exc
    except Exception as exc:
        raise PolymarketAPIError(
            msg=f"Failed to {action}: {exc}",
            status_code=_HTTP_INTERNAL_ERROR,
        ) from exc


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
    raw = _safe_clob_call(
        f"fetch order book for {token_id}",
        client.get_order_book,
        token_id,
        allow_404=True,
    )
    return _normalize_order_book(raw) if raw is not None else None


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
    result: str | None = _safe_clob_call(
        f"fetch price for {token_id}",
        client.get_price,
        token_id,
        side,
        allow_404=True,
    )
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
    raw = _safe_clob_call(
        f"fetch midpoint for {token_id}",
        client.get_midpoint,
        token_id,
        allow_404=True,
    )
    return _extract_midpoint(raw) if raw is not None else None


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
    return _safe_clob_call(
        f"fetch market {condition_id}",
        client.get_market,
        condition_id,
        allow_404=True,
    )


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
    result: str | None = _safe_clob_call(
        f"fetch last trade price for {token_id}",
        client.get_last_trade_price,
        token_id,
        allow_404=True,
    )
    return result


_POLYGON_CHAIN_ID = 137


def derive_funder_address(private_key: str) -> str:
    """Derive the EOA address from a private key.

    The funder address is required for proxy wallet order signatures.
    It is the Ethereum address corresponding to the given private key.

    Args:
        private_key: Hex-encoded private key (with ``0x`` prefix).

    Returns:
        Checksummed Ethereum address string.

    """
    return Account.from_key(private_key).address  # type: ignore[no-any-return]


def create_authenticated_clob_client(
    host: str,
    private_key: str,
    chain_id: int = _POLYGON_CHAIN_ID,
    creds: tuple[str, str, str] | None = None,
    funder: str | None = None,
) -> ClobClient:  # type: ignore[no-any-unimported]
    """Create an authenticated CLOB client for trading.

    When ``creds`` are provided, create a Level 2 client that can post
    orders and check balances.  Without ``creds``, create a Level 1 client
    that can derive API credentials.

    The ``funder`` address is the Polymarket proxy wallet contract that
    holds funds.  When omitted, the EOA address derived from the private
    key is used (which only works for non-proxy-wallet accounts).

    Args:
        host: Base URL for the Polymarket CLOB API.
        private_key: Polygon wallet private key (hex string with ``0x`` prefix).
        chain_id: Blockchain chain ID (default 137 for Polygon mainnet).
        creds: Optional tuple of ``(api_key, api_secret, api_passphrase)``
            for Level 2 authentication.
        funder: Proxy wallet address that holds the trading funds.  If
            ``None``, falls back to the EOA address derived from the key.

    Returns:
        Configured ``ClobClient`` ready for authenticated API calls.

    """
    resolved_funder = funder or derive_funder_address(private_key)
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
            signature_type=_POLYGON_PROXY_WALLET,
            funder=resolved_funder,
        )
    return ClobClient(
        host,
        chain_id=chain_id,
        key=private_key,
        signature_type=_POLYGON_PROXY_WALLET,
        funder=resolved_funder,
    )  # type: ignore[no-any-return]


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
    raw = _safe_clob_call("derive API credentials", client.derive_api_key)
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

    def _create_and_post() -> dict[str, Any]:
        order = client.create_order(
            order_args=OrderArgs(
                token_id=token_id,
                price=price,
                size=size,
                side=side,
            ),
            options=PartialCreateOrderOptions(tick_size="0.01"),
        )
        return client.post_order(order, orderType=OrderType.GTC)  # type: ignore[no-any-return]

    return _safe_clob_call("place limit order", _create_and_post)


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

    def _create_and_post() -> dict[str, Any]:
        order = client.create_market_order(
            order_args=MarketOrderArgs(
                token_id=token_id,
                amount=amount,
                side=side,
            ),
            options=PartialCreateOrderOptions(tick_size="0.01"),
        )
        return client.post_order(order, orderType=OrderType.FOK)  # type: ignore[no-any-return]

    return _safe_clob_call("place market order", _create_and_post)


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
    params = BalanceAllowanceParams(asset_type=resolved_type)  # type: ignore[reportArgumentType]

    def _fetch() -> dict[str, Any]:
        return client.get_balance_allowance(params=params)  # type: ignore[no-any-return]

    return _safe_clob_call("fetch balance", _fetch)


def update_balance(client: Any, asset_type: str = "COLLATERAL") -> None:
    """Tell the CLOB to re-sync its cached balance from on-chain state.

    Call this before ``get_balance`` so the CLOB returns the latest on-chain
    USDC balance rather than a stale cached value.

    Args:
        client: A Level 2 ``ClobClient`` instance.
        asset_type: ``"COLLATERAL"`` for USDC or ``"CONDITIONAL"`` for tokens.

    Raises:
        PolymarketAPIError: When the update call fails.

    """
    resolved_type: str = (
        AssetType.COLLATERAL if asset_type == "COLLATERAL" else AssetType.CONDITIONAL
    )
    params = BalanceAllowanceParams(asset_type=resolved_type)  # type: ignore[reportArgumentType]

    def _update() -> dict[str, Any]:
        return client.update_balance_allowance(params=params)  # type: ignore[no-any-return]

    _safe_clob_call("update balance", _update)


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
    return _safe_clob_call(f"cancel order {order_id}", client.cancel, order_id)


def get_open_orders(client: Any) -> list[dict[str, Any]]:
    """Fetch all open orders for the authenticated user.

    Args:
        client: A Level 2 ``ClobClient`` instance.

    Returns:
        List of open order dictionaries.

    Raises:
        PolymarketAPIError: When the query fails.

    """

    def _fetch() -> Any:
        return client.get_orders(params=OpenOrderParams())

    result = _safe_clob_call("fetch open orders", _fetch)
    if isinstance(result, list):
        return cast("list[dict[str, Any]]", result)
    return []
