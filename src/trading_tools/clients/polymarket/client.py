"""Typed async facade for Polymarket prediction market data.

Compose the synchronous CLOB adapter and the async Gamma client into
a single async interface.  Synchronous CLOB calls are wrapped in
``asyncio.to_thread()`` to avoid blocking the event loop.
"""

import asyncio
import json
from decimal import Decimal, InvalidOperation
from typing import Any

from trading_tools.clients.polymarket import _clob_adapter
from trading_tools.clients.polymarket._gamma_client import GammaClient
from trading_tools.clients.polymarket.exceptions import PolymarketAPIError
from trading_tools.clients.polymarket.models import (
    Market,
    MarketToken,
    OrderBook,
    OrderLevel,
)

_ZERO = Decimal(0)
_TWO = Decimal(2)


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

    def __init__(
        self,
        host: str = CLOB_HOST,
        gamma_base_url: str = GAMMA_URL,
    ) -> None:
        """Initialize the Polymarket client.

        Args:
            host: Base URL for the Polymarket CLOB API.
            gamma_base_url: Base URL for the Gamma metadata API.

        """
        self._clob_client: Any = _clob_adapter.create_clob_client(host)
        self._gamma = GammaClient(base_url=gamma_base_url)
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

        # Enrich tokens with live CLOB midpoint prices
        enriched_tokens: list[MarketToken] = []
        for token in market.tokens:
            async with self._clob_lock:
                price = await asyncio.to_thread(
                    _clob_adapter.fetch_midpoint, self._clob_client, token.token_id
                )
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

    async def close(self) -> None:
        """Close underlying HTTP clients."""
        await self._gamma.close()

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
