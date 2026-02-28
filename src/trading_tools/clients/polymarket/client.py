"""Typed async facade for Polymarket prediction market data.

Compose the synchronous CLOB adapter and the async Gamma client into
a single async interface.  Synchronous CLOB calls are wrapped in
``asyncio.to_thread()`` to avoid blocking the event loop.
"""

import asyncio
import json
import logging
import os
import time
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from trading_tools.clients.polymarket import _clob_adapter, _ctf_redeemer
from trading_tools.clients.polymarket._gamma_client import GammaClient
from trading_tools.clients.polymarket.exceptions import PolymarketAPIError
from trading_tools.clients.polymarket.models import (
    Balance,
    Market,
    MarketToken,
    OrderBook,
    OrderLevel,
    OrderRequest,
    OrderResponse,
    RedeemablePosition,
)

logger = logging.getLogger(__name__)

_ZERO = Decimal(0)
_TWO = Decimal(2)
_USDC_DECIMALS = Decimal("1e6")
_HTTP_BAD_REQUEST = 400


class PolymarketClient:
    """Typed async client for Polymarket prediction markets.

    Combine market metadata from the Gamma API with live order book
    and pricing data from the CLOB API.  All public methods are async
    and return typed dataclasses.

    Args:
        host: Base URL for the Polymarket CLOB API.
        gamma_base_url: Base URL for the Gamma metadata API.

    """

    CLOB_HOST = "https://clob.polymarket.com"
    GAMMA_URL = "https://gamma-api.polymarket.com"
    DATA_API_URL = "https://data-api.polymarket.com"

    def __init__(
        self,
        host: str = CLOB_HOST,
        gamma_base_url: str = GAMMA_URL,
        private_key: str | None = None,
        api_key: str | None = None,
        api_secret: str | None = None,
        api_passphrase: str | None = None,
        funder_address: str | None = None,
    ) -> None:
        """Initialize the Polymarket client.

        When ``private_key`` is provided, create an authenticated client
        capable of placing trades.  If API credentials are also provided,
        skip the key derivation step and connect at Level 2 immediately.
        Without a private key the client operates in read-only mode.

        Args:
            host: Base URL for the Polymarket CLOB API.
            gamma_base_url: Base URL for the Gamma metadata API.
            private_key: Polygon wallet private key (hex ``0x…`` string).
            api_key: Pre-existing CLOB API key.
            api_secret: Pre-existing CLOB API secret.
            api_passphrase: Pre-existing CLOB API passphrase.
            funder_address: Proxy wallet address holding the trading funds.
                Required for Polymarket UI-funded (proxy wallet) accounts.

        """
        self._private_key = private_key
        self._funder_address = funder_address
        self._authenticated = private_key is not None
        if private_key is not None:
            creds = (
                (api_key, api_secret, api_passphrase)
                if api_key and api_secret and api_passphrase
                else None
            )
            self._clob_client: Any = _clob_adapter.create_authenticated_clob_client(
                host, private_key, creds=creds, funder=funder_address
            )
        else:
            self._clob_client = _clob_adapter.create_clob_client(host)
        self._gamma = GammaClient(base_url=gamma_base_url)
        self._data_client = httpx.AsyncClient(timeout=30.0)
        self._clob_lock = asyncio.Lock()

    async def search_markets(
        self,
        keyword: str = "Bitcoin",
        *,
        limit: int = 20,
    ) -> list[Market]:
        """Search for prediction markets matching a keyword.

        Fetch markets from the Gamma API and filter client-side on the
        ``question`` field (case-insensitive substring match).

        Args:
            keyword: Search term to match against market questions.
            limit: Maximum number of results to return.

        Returns:
            List of matching markets with current pricing data.

        """
        raw_markets = await self._gamma.get_markets(active=True, limit=100)
        keyword_lower = keyword.lower()
        matches: list[Market] = []
        for raw in raw_markets:
            question: str = raw.get("question", "")
            if keyword_lower not in question.lower():
                continue
            matches.append(self._parse_market(raw))
            if len(matches) >= limit:
                break
        return matches

    async def get_market(self, condition_id: str) -> Market:
        """Fetch a single market with live CLOB pricing.

        Use the CLOB ``/markets/{condition_id}`` endpoint for reliable
        lookup, then enrich token prices with live midpoint data.

        Args:
            condition_id: Unique identifier for the market condition.

        Returns:
            Market with current token prices from the CLOB API.

        Raises:
            PolymarketAPIError: When the market is not found or API fails.

        """
        async with self._clob_lock:
            raw = await asyncio.to_thread(
                _clob_adapter.fetch_market, self._clob_client, condition_id
            )
        if raw is None:
            raise PolymarketAPIError(
                msg=f"Market not found: {condition_id}",
                status_code=404,
            )
        market = self._parse_clob_market(raw)

        # Enrich tokens with live CLOB midpoint prices (concurrent fetches)
        async def _fetch_midpoint(token_id: str) -> str | None:
            async with self._clob_lock:
                return await asyncio.to_thread(
                    _clob_adapter.fetch_midpoint, self._clob_client, token_id
                )

        midpoints = await asyncio.gather(
            *(_fetch_midpoint(token.token_id) for token in market.tokens)
        )
        enriched_tokens: list[MarketToken] = []
        for token, price in zip(market.tokens, midpoints, strict=True):
            live_price = _safe_decimal(price) if price is not None else token.price
            enriched_tokens.append(
                MarketToken(
                    token_id=token.token_id,
                    outcome=token.outcome,
                    price=live_price,
                )
            )
        return Market(
            condition_id=market.condition_id,
            question=market.question,
            description=market.description,
            tokens=tuple(enriched_tokens),
            end_date=market.end_date,
            volume=market.volume,
            liquidity=market.liquidity,
            active=market.active,
        )

    async def get_order_book(self, token_id: str) -> OrderBook:
        """Fetch a typed order book for a token.

        Args:
            token_id: CLOB token identifier.

        Returns:
            Typed order book with bids, asks, spread, and midpoint.

        Raises:
            PolymarketAPIError: When the CLOB API call fails.

        """
        async with self._clob_lock:
            raw = await asyncio.to_thread(
                _clob_adapter.fetch_order_book,
                self._clob_client,
                token_id,
            )
        if raw is None:
            return OrderBook(token_id=token_id, bids=(), asks=(), spread=_ZERO, midpoint=_ZERO)
        return self._parse_order_book(token_id, raw)

    async def discover_series_markets(
        self,
        series_slugs: list[str],
    ) -> list[tuple[str, str]]:
        """Discover active markets from event series slugs.

        Query the Gamma API events endpoint for each series slug and collect
        the condition IDs and precise end dates of all active markets within
        those events.

        For rotating short-duration markets (e.g. ``btc-updown-5m``), the
        Gamma API slug includes an epoch timestamp suffix. This method
        automatically computes the current 5-minute window epoch and appends
        it when the base slug matches the ``*-5m`` pattern.

        Args:
            series_slugs: Event slugs to search (e.g. ``["btc-updown-5m"]``).

        Returns:
            List of ``(condition_id, end_date_iso)`` tuples for active markets
            found in the specified series.

        """
        resolved_slugs = _resolve_timestamped_slugs(series_slugs)
        all_events = await asyncio.gather(
            *(self._gamma.get_events(slug=slug, active=True, limit=5) for slug in resolved_slugs)
        )
        results: list[tuple[str, str]] = []
        for events in all_events:
            for event in events:
                for market_raw in event.get("markets", []):
                    if not market_raw.get("active", False):
                        continue
                    cid = market_raw.get("conditionId", market_raw.get("condition_id", ""))
                    end_date = market_raw.get("endDate", market_raw.get("end_date", ""))
                    if cid:
                        results.append((cid, end_date))
        return results

    def _require_auth(self) -> None:
        """Raise an error if the client is not authenticated.

        Raises:
            PolymarketAPIError: When no private key was provided at init.

        """
        if not self._authenticated:
            raise PolymarketAPIError(
                msg="Authentication required. Provide a private key to enable trading.",
                status_code=401,
            )

    async def derive_api_creds(self) -> tuple[str, str, str]:
        """Derive API credentials from the wallet's private key.

        Perform a one-time derivation to obtain HMAC credentials for
        Level 2 authentication.  Store the returned values for reuse.

        Returns:
            Tuple of ``(api_key, api_secret, api_passphrase)``.

        Raises:
            PolymarketAPIError: When not authenticated or derivation fails.

        """
        self._require_auth()
        async with self._clob_lock:
            return await asyncio.to_thread(_clob_adapter.derive_api_creds, self._clob_client)

    async def place_order(self, request: OrderRequest) -> OrderResponse:
        """Place a limit or market order on Polymarket.

        Dispatch to the appropriate adapter function based on the
        ``order_type`` field of the request.

        Args:
            request: Typed order request with token, side, price, and size.

        Returns:
            Typed order response with ID, status, and fill information.

        Raises:
            PolymarketAPIError: When not authenticated or the order fails.

        """
        self._require_auth()
        if request.order_type == "market":
            async with self._clob_lock:
                raw = await asyncio.to_thread(
                    _clob_adapter.place_market_order,
                    self._clob_client,
                    request.token_id,
                    request.side,
                    float(request.size),
                )
        else:
            async with self._clob_lock:
                raw = await asyncio.to_thread(
                    _clob_adapter.place_limit_order,
                    self._clob_client,
                    request.token_id,
                    request.side,
                    float(request.price),
                    float(request.size),
                )
        return _parse_order_response(raw, request)

    async def sync_balance(self, asset_type: str = "COLLATERAL") -> None:
        """Tell the CLOB to re-sync its cached balance from on-chain state.

        Call this before ``get_balance`` so the returned value reflects the
        latest on-chain USDC balance rather than a stale cached value.

        Args:
            asset_type: ``"COLLATERAL"`` for USDC or ``"CONDITIONAL"`` for tokens.

        Raises:
            PolymarketAPIError: When not authenticated or the sync fails.

        """
        self._require_auth()
        async with self._clob_lock:
            await asyncio.to_thread(_clob_adapter.update_balance, self._clob_client, asset_type)

    async def get_balance(self, asset_type: str = "COLLATERAL") -> Balance:
        """Fetch the balance and allowance for an asset.

        Args:
            asset_type: ``"COLLATERAL"`` for USDC or ``"CONDITIONAL"`` for tokens.

        Returns:
            Typed balance with balance and allowance amounts.

        Raises:
            PolymarketAPIError: When not authenticated or the query fails.

        """
        self._require_auth()
        async with self._clob_lock:
            raw = await asyncio.to_thread(_clob_adapter.get_balance, self._clob_client, asset_type)
        raw_balance = _safe_decimal(raw.get("balance"))
        raw_allowance = _safe_decimal(raw.get("allowance"))
        return Balance(
            asset_type=asset_type,
            balance=raw_balance / _USDC_DECIMALS,
            allowance=raw_allowance / _USDC_DECIMALS,
        )

    async def cancel_order(self, order_id: str) -> dict[str, Any]:
        """Cancel an open order.

        Args:
            order_id: Identifier of the order to cancel.

        Returns:
            Raw API response confirming the cancellation.

        Raises:
            PolymarketAPIError: When not authenticated or cancellation fails.

        """
        self._require_auth()
        async with self._clob_lock:
            return await asyncio.to_thread(_clob_adapter.cancel_order, self._clob_client, order_id)

    async def get_open_orders(self) -> list[OrderResponse]:
        """Fetch all open orders for the authenticated user.

        Returns:
            List of typed order responses.

        Raises:
            PolymarketAPIError: When not authenticated or the query fails.

        """
        self._require_auth()
        async with self._clob_lock:
            raw_list = await asyncio.to_thread(_clob_adapter.get_open_orders, self._clob_client)
        return [_parse_raw_order(raw) for raw in raw_list]

    async def get_redeemable_positions(self) -> list[RedeemablePosition]:
        """Discover redeemable positions via the Polymarket Data API.

        Query the unauthenticated Data API for all positions held by the
        proxy wallet that are marked as redeemable (resolved and winning).

        Returns:
            List of redeemable positions with token IDs and sizes.

        Raises:
            PolymarketAPIError: When the funder address is not configured
                or the Data API request fails.

        """
        if not self._funder_address:
            raise PolymarketAPIError(
                msg="Funder address required for position discovery. "
                "Set POLYMARKET_FUNDER_ADDRESS.",
                status_code=0,
            )
        url = f"{self.DATA_API_URL}/positions"
        params: dict[str, str] = {
            "user": self._funder_address,
            "redeemable": "true",
            "sizeThreshold": "0",
        }
        try:
            response = await self._data_client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise PolymarketAPIError(
                msg=f"Data API request failed: {exc}",
                status_code=0,
            ) from exc

        if response.status_code >= _HTTP_BAD_REQUEST:
            raise PolymarketAPIError(
                msg=f"Data API error: HTTP {response.status_code}",
                status_code=response.status_code,
            )

        raw_positions: list[dict[str, Any]] = response.json()
        results: list[RedeemablePosition] = []
        for raw in raw_positions:
            size = _safe_decimal(raw.get("size", "0"))
            if size <= _ZERO:
                continue
            results.append(
                RedeemablePosition(
                    condition_id=str(raw.get("conditionId", "")),
                    token_id=str(raw.get("asset", "")),
                    outcome=str(raw.get("outcome", "")),
                    size=size,
                    title=str(raw.get("title", "")),
                )
            )
        return results

    async def redeem_positions(
        self,
        condition_ids: list[str],
        rpc_url: str = "",
    ) -> int:
        """Redeem winning positions for resolved markets.

        Call ``redeemPositions`` on the CTF contract through the Polymarket
        ProxyWalletFactory.  Require POL in the signing EOA for gas (typically
        less than $0.01 per redemption on Polygon).

        Args:
            condition_ids: List of resolved market condition IDs to redeem.
            rpc_url: Polygon JSON-RPC endpoint URL.  Falls back to the
                ``POLYGON_RPC_URL`` environment variable, then the public
                QuikNode endpoint.

        Returns:
            Number of successfully redeemed positions.  Individual failures
            are logged and skipped so remaining positions are still attempted.

        Raises:
            PolymarketAPIError: When not authenticated or the RPC is unreachable.

        """
        self._require_auth()
        if not self._private_key or not condition_ids:
            return 0
        resolved_rpc = rpc_url or os.environ.get(
            "POLYGON_RPC_URL", "https://rpc-mainnet.matic.quiknode.pro"
        )
        receipts = await asyncio.to_thread(
            _ctf_redeemer.redeem_positions,
            resolved_rpc,
            self._private_key,
            condition_ids,
        )
        return sum(1 for r in receipts if r["status"] == 1)

    async def close(self) -> None:
        """Close underlying HTTP clients."""
        await self._gamma.close()
        await self._data_client.aclose()

    async def __aenter__(self) -> "PolymarketClient":
        """Enter async context manager."""
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Exit async context manager."""
        await self.close()

    @staticmethod
    def _parse_clob_market(raw: dict[str, Any]) -> Market:
        """Convert a raw CLOB API market dict into a typed Market.

        The CLOB API uses snake_case keys and embeds tokens as a list of
        dicts with ``token_id``, ``outcome``, and ``price`` fields.

        Args:
            raw: Market dictionary from the CLOB ``/markets/`` endpoint.

        Returns:
            Typed Market dataclass.

        """
        tokens = [
            MarketToken(
                token_id=str(t.get("token_id", "")),
                outcome=str(t.get("outcome", "")),
                price=_safe_decimal(t.get("price", "0")),
            )
            for t in raw.get("tokens", [])
        ]
        return Market(
            condition_id=raw.get("condition_id", ""),
            question=raw.get("question", ""),
            description=raw.get("description", ""),
            tokens=tuple(tokens),
            end_date=raw.get("end_date_iso", ""),
            volume=_ZERO,  # CLOB endpoint doesn't include volume
            liquidity=_ZERO,  # CLOB endpoint doesn't include liquidity
            active=bool(raw.get("active", False)),
        )

    @staticmethod
    def _parse_market(raw: dict[str, Any]) -> Market:
        """Convert a raw Gamma API market dict into a typed Market.

        Args:
            raw: Market dictionary from the Gamma API.

        Returns:
            Typed Market dataclass.

        """
        tokens = _parse_tokens(raw)
        return Market(
            condition_id=raw.get("conditionId", raw.get("condition_id", "")),
            question=raw.get("question", ""),
            description=raw.get("description", ""),
            tokens=tuple(tokens),
            end_date=raw.get("endDate", raw.get("end_date", "")),
            volume=_safe_decimal(raw.get("volume", "0")),
            liquidity=_safe_decimal(raw.get("liquidity", "0")),
            active=bool(raw.get("active", False)),
        )

    @staticmethod
    def _parse_order_book(token_id: str, raw: dict[str, Any]) -> OrderBook:
        """Convert a raw CLOB order book dict into a typed OrderBook.

        Args:
            token_id: CLOB token identifier.
            raw: Raw order book dictionary with ``bids`` and ``asks``.

        Returns:
            Typed OrderBook dataclass.

        """
        bids = tuple(
            sorted(
                (
                    OrderLevel(
                        price=_safe_decimal(level.get("price", "0")),
                        size=_safe_decimal(level.get("size", "0")),
                    )
                    for level in raw.get("bids", [])
                ),
                key=lambda lvl: lvl.price,
                reverse=True,
            )
        )
        asks = tuple(
            sorted(
                (
                    OrderLevel(
                        price=_safe_decimal(level.get("price", "0")),
                        size=_safe_decimal(level.get("size", "0")),
                    )
                    for level in raw.get("asks", [])
                ),
                key=lambda lvl: lvl.price,
            )
        )

        best_bid = bids[0].price if bids else _ZERO
        best_ask = asks[0].price if asks else _ZERO
        spread = best_ask - best_bid if best_bid and best_ask else _ZERO
        midpoint = (best_bid + best_ask) / _TWO if best_bid and best_ask else _ZERO

        return OrderBook(
            token_id=token_id,
            bids=bids,
            asks=asks,
            spread=spread,
            midpoint=midpoint,
        )


def _parse_tokens(raw: dict[str, Any]) -> list[MarketToken]:
    """Extract outcome tokens from a raw Gamma API market dictionary.

    Handle the Gamma API's convention of encoding ``outcomePrices`` and
    ``clobTokenIds`` as JSON strings within the response.

    Args:
        raw: Market dictionary from the Gamma API.

    Returns:
        List of typed MarketToken instances.

    """
    outcomes_raw = raw.get("outcomes", "")
    if isinstance(outcomes_raw, str):
        try:
            outcomes: list[str] = json.loads(outcomes_raw)
        except (json.JSONDecodeError, TypeError):
            outcomes = []
    else:
        outcomes = list(outcomes_raw)

    prices_raw = raw.get("outcomePrices", "")
    if isinstance(prices_raw, str):
        try:
            prices: list[str] = json.loads(prices_raw)
        except (json.JSONDecodeError, TypeError):
            prices = []
    else:
        prices = list(prices_raw)

    token_ids_raw = raw.get("clobTokenIds", "")
    if isinstance(token_ids_raw, str):
        try:
            token_ids: list[str] = json.loads(token_ids_raw)
        except (json.JSONDecodeError, TypeError):
            token_ids = []
    else:
        token_ids = list(token_ids_raw)

    tokens: list[MarketToken] = []
    for i, outcome in enumerate(outcomes):
        price = _safe_decimal(prices[i]) if i < len(prices) else _ZERO
        token_id = token_ids[i] if i < len(token_ids) else ""
        tokens.append(MarketToken(token_id=token_id, outcome=outcome, price=price))
    return tokens


def _safe_decimal(value: Any) -> Decimal:
    """Convert a value to Decimal, returning zero for None/empty strings.

    Raise ``PolymarketAPIError`` for values that are present but
    cannot be parsed into a valid Decimal — this avoids silently
    substituting zero for genuinely corrupt data.

    Args:
        value: Value to convert (string, float, int, or None).

    Returns:
        Decimal representation, or ``Decimal("0")`` for None/empty.

    Raises:
        PolymarketAPIError: If the value is non-empty but malformed.

    """
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return _ZERO
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        msg = f"Cannot convert {value!r} to Decimal"
        raise PolymarketAPIError(msg=msg, status_code=0) from exc


_FIVE_MINUTES = 300


def _resolve_timestamped_slugs(series_slugs: list[str]) -> list[str]:
    """Expand series slugs into timestamped slugs for rotating markets.

    Polymarket 5-minute markets use slugs like ``btc-updown-5m-1771758600``
    where the suffix is the Unix epoch of the current 5-minute window start.
    For slugs ending in ``-5m``, compute the current window epoch and append
    it. Other slugs are passed through unchanged.

    Args:
        series_slugs: Base series slugs (e.g. ``["btc-updown-5m"]``).

    Returns:
        Resolved slugs with epoch suffixes where applicable.

    """
    now = int(time.time())
    current_window = (now // _FIVE_MINUTES) * _FIVE_MINUTES
    resolved: list[str] = []
    for slug in series_slugs:
        if slug.endswith("-5m"):
            resolved.append(f"{slug}-{current_window}")
        else:
            resolved.append(slug)
    return resolved


def _parse_order_response(raw: dict[str, Any], request: OrderRequest) -> OrderResponse:
    """Convert a raw CLOB API order response into a typed OrderResponse.

    The API may return different key formats depending on the endpoint.
    Fall back to the request values for fields not present in the response.

    Args:
        raw: Raw dictionary from the CLOB ``post_order`` call.
        request: Original order request used for fallback values.

    Returns:
        Typed OrderResponse dataclass.

    """
    return OrderResponse(
        order_id=str(raw.get("orderID", raw.get("id", ""))),
        status=str(raw.get("status", "unknown")),
        token_id=request.token_id,
        side=request.side,
        price=request.price,
        size=request.size,
        filled=_safe_decimal(raw.get("filled", "0")),
    )


def _parse_raw_order(raw: dict[str, Any]) -> OrderResponse:
    """Convert a raw open order dictionary into a typed OrderResponse.

    Args:
        raw: Order dictionary from the CLOB ``get_orders`` endpoint.

    Returns:
        Typed OrderResponse dataclass.

    """
    return OrderResponse(
        order_id=str(raw.get("id", raw.get("orderID", ""))),
        status=str(raw.get("status", "unknown")),
        token_id=str(raw.get("asset_id", raw.get("token_id", ""))),
        side=str(raw.get("side", "")),
        price=_safe_decimal(raw.get("price", "0")),
        size=_safe_decimal(raw.get("original_size", raw.get("size", "0"))),
        filled=_safe_decimal(raw.get("size_matched", raw.get("filled", "0"))),
    )
