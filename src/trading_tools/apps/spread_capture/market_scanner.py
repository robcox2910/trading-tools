"""Market scanner for spread capture opportunities.

Periodically discover active Up/Down markets from series slugs and check
CLOB prices for spread opportunities where the combined cost of buying
both sides is below the configured threshold.  Deduct Polymarket fees
from the gross margin to filter out unprofitable opportunities.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import httpx

from trading_tools.apps.spread_capture.fees import compute_poly_fee
from trading_tools.apps.spread_capture.models import SpreadOpportunity
from trading_tools.apps.whale_monitor.correlator import parse_asset, parse_time_window
from trading_tools.clients.polymarket.exceptions import PolymarketError
from trading_tools.core.models import ONE, ZERO

if TYPE_CHECKING:
    from trading_tools.clients.polymarket.client import PolymarketClient
    from trading_tools.clients.polymarket.models import Market, OrderBook

logger = logging.getLogger(__name__)


def _is_expired(end_date: str, now_epoch: int) -> bool:
    """Return True if the ISO-8601 end_date is in the past.

    Args:
        end_date: ISO-8601 date string from the Gamma API.
        now_epoch: Current epoch seconds.

    Returns:
        ``True`` if the market has expired.

    """
    try:
        dt = datetime.fromisoformat(end_date)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.timestamp() < now_epoch
    except (ValueError, OSError):
        return False


def _empty_market_dict() -> dict[str, _KnownMarket]:
    """Return an empty dict for dataclass default_factory."""
    return {}


@dataclass(frozen=True)
class _KnownMarket:
    """Cached metadata for a discovered market.

    Attributes:
        condition_id: Polymarket market condition identifier.
        end_date: ISO-8601 end date string from Gamma API.

    """

    condition_id: str
    end_date: str


@dataclass
class MarketScanner:
    """Scan Polymarket series for spread capture opportunities.

    Periodically call ``discover_series_markets()`` to refresh the set
    of known active markets, then fetch CLOB prices for each and emit
    ``SpreadOpportunity`` instances where the combined cost is below
    the configured threshold.

    Attributes:
        client: Authenticated Polymarket client for CLOB and Gamma API.
        series_slugs: Event series slugs to scan.
        max_combined_cost: Maximum combined cost to trigger an entry.
        min_spread_margin: Minimum profit margin per token pair after fees.
        max_window_seconds: Maximum market window duration (0 = no limit).
        max_entry_age_pct: Maximum fraction of window elapsed.
        rediscovery_interval: Seconds between market rediscovery calls.
        fee_rate: Polymarket fee rate coefficient for margin deduction.
        fee_exponent: Exponent for the fee formula.

    """

    client: PolymarketClient
    series_slugs: tuple[str, ...]
    max_combined_cost: Decimal
    min_spread_margin: Decimal
    max_window_seconds: int
    max_entry_age_pct: Decimal
    rediscovery_interval: int
    fee_rate: Decimal = Decimal("0.25")
    fee_exponent: int = 2
    _known_markets: dict[str, _KnownMarket] = field(default_factory=_empty_market_dict, repr=False)
    _last_discovery: float = field(default=0.0, repr=False)

    async def scan(self, open_cids: set[str]) -> list[SpreadOpportunity]:
        """Scan for spread opportunities across known markets.

        Periodically rediscover markets, then for each active market not
        already open, fetch CLOB prices and check if the combined cost
        is below the threshold.

        Args:
            open_cids: Condition IDs of currently open positions (skipped).

        Returns:
            List of actionable spread opportunities sorted by margin
            (highest first).

        """
        await self._maybe_rediscover()

        now = int(time.time())

        cids_to_scan = [cid for cid in self._known_markets if cid not in open_cids]
        results = await asyncio.gather(
            *(self._evaluate_market(cid, now) for cid in cids_to_scan),
            return_exceptions=True,
        )
        opportunities = [r for r in results if isinstance(r, SpreadOpportunity)]

        # Sort by margin (highest first)
        opportunities.sort(key=lambda o: o.margin, reverse=True)
        return opportunities

    async def _evaluate_market(self, cid: str, now: int) -> SpreadOpportunity | None:
        """Evaluate a single market for spread opportunity.

        Fetch the market metadata (for title, active status, and token IDs),
        then fetch order books for both tokens concurrently and use the best
        ask prices.  Best asks represent the actual cost to buy each side.

        Args:
            cid: Market condition identifier.
            now: Current epoch seconds.

        Returns:
            A ``SpreadOpportunity`` if the market is tradeable, else ``None``.

        """
        try:
            market = await self.client.get_market(cid)
        except (PolymarketError, httpx.HTTPError, KeyError, ValueError):
            logger.debug("Failed to fetch market %s", cid[:12])
            return None

        if not market.active:
            return None

        # Parse asset and time window from the market title
        asset = parse_asset(market.question)
        if asset is None:
            return None

        window = parse_time_window(market.question, now)
        if window is None:
            return None

        # Fetch order books for both tokens to get best ask prices
        tokens_by_outcome = {t.outcome: t for t in market.tokens}
        up_token = tokens_by_outcome.get("Up")
        down_token = tokens_by_outcome.get("Down")
        if up_token is None or down_token is None:
            return None

        up_book, down_book = await self._fetch_order_books(up_token.token_id, down_token.token_id)

        # Use best ask price (actual buy cost); fall back to midpoint if no asks
        up_ask = up_book.asks[0].price if up_book.asks else up_token.price
        down_ask = down_book.asks[0].price if down_book.asks else down_token.price

        # Compute total visible ask-side depth for market impact cap
        up_ask_depth = sum((level.size for level in up_book.asks), start=ZERO)
        down_ask_depth = sum((level.size for level in down_book.asks), start=ZERO)

        return self._check_spread(
            market,
            cid,
            asset,
            window[0],
            window[1],
            now,
            up_ask_price=up_ask,
            down_ask_price=down_ask,
            up_ask_depth=up_ask_depth,
            down_ask_depth=down_ask_depth,
        )

    def _check_spread(
        self,
        market: Market,
        cid: str,
        asset: str,
        window_start_ts: int,
        window_end_ts: int,
        now: int,
        *,
        up_ask_price: Decimal | None = None,
        down_ask_price: Decimal | None = None,
        up_ask_depth: Decimal = ZERO,
        down_ask_depth: Decimal = ZERO,
    ) -> SpreadOpportunity | None:
        """Check whether a market has a viable spread opportunity.

        Apply window duration, entry age, price, and margin filters.
        Deduct Polymarket fees from the gross margin to ensure the
        opportunity is profitable net of fees.

        Args:
            market: The fetched market with token prices.
            cid: Market condition identifier.
            asset: Parsed asset symbol (e.g. ``"BTC-USD"``).
            window_start_ts: Window start epoch seconds.
            window_end_ts: Window end epoch seconds.
            now: Current epoch seconds.
            up_ask_price: Best ask price for Up token (order book).
            down_ask_price: Best ask price for Down token (order book).
            up_ask_depth: Total visible ask depth for Up token.
            down_ask_depth: Total visible ask depth for Down token.

        Returns:
            A ``SpreadOpportunity`` if viable, else ``None``.

        """
        if window_end_ts <= now:
            return None

        window_duration = window_end_ts - window_start_ts
        if self.max_window_seconds > 0 and window_duration > self.max_window_seconds:
            return None

        if self.max_entry_age_pct > ZERO and window_duration > 0:
            elapsed_pct = Decimal(str(now - window_start_ts)) / Decimal(str(window_duration))
            if elapsed_pct > self.max_entry_age_pct:
                return None

        tokens_by_outcome = {t.outcome: t for t in market.tokens}
        up_token = tokens_by_outcome.get("Up")
        down_token = tokens_by_outcome.get("Down")

        if up_token is None or down_token is None:
            return None

        # Use order book best ask prices when available, else midpoint
        up_price = up_ask_price if up_ask_price is not None else up_token.price
        down_price = down_ask_price if down_ask_price is not None else down_token.price

        if up_price <= ZERO or down_price <= ZERO:
            return None

        combined = up_price + down_price
        gross_margin = ONE - combined

        if combined >= self.max_combined_cost:
            return None

        # Deduct Polymarket fees from margin
        up_fee = compute_poly_fee(up_price, self.fee_rate, self.fee_exponent)
        down_fee = compute_poly_fee(down_price, self.fee_rate, self.fee_exponent)
        net_margin = gross_margin - up_fee - down_fee

        if net_margin < self.min_spread_margin:
            return None

        return SpreadOpportunity(
            condition_id=cid,
            title=market.question,
            asset=asset,
            up_token_id=up_token.token_id,
            down_token_id=down_token.token_id,
            up_price=up_price,
            down_price=down_price,
            combined=combined,
            margin=net_margin,
            window_start_ts=window_start_ts,
            window_end_ts=window_end_ts,
            up_ask_depth=up_ask_depth,
            down_ask_depth=down_ask_depth,
        )

    async def _fetch_order_books(
        self, up_token_id: str, down_token_id: str
    ) -> tuple[OrderBook, OrderBook]:
        """Fetch order books for both tokens concurrently.

        Args:
            up_token_id: CLOB token ID for the Up outcome.
            down_token_id: CLOB token ID for the Down outcome.

        Returns:
            Tuple of (up_order_book, down_order_book).

        """
        up_book, down_book = await asyncio.gather(
            self.client.get_order_book(up_token_id),
            self.client.get_order_book(down_token_id),
        )
        return up_book, down_book

    async def _maybe_rediscover(self) -> None:
        """Refresh the known markets set if the rediscovery interval has elapsed."""
        now = time.monotonic()
        if now - self._last_discovery < self.rediscovery_interval:
            return

        try:
            discovered = await self.client.discover_series_markets(
                list(self.series_slugs), include_next=True
            )
        except (PolymarketError, httpx.HTTPError, KeyError, ValueError):
            logger.warning("Market rediscovery failed")
            return

        # Bug fix #1: compute new count BEFORE insertion (otherwise they
        # are already in _known_markets and the count is always 0).
        new_count = sum(1 for c, _ in discovered if c not in self._known_markets)

        for cid, end_date in discovered:
            if cid not in self._known_markets:
                self._known_markets[cid] = _KnownMarket(condition_id=cid, end_date=end_date)

        # Bug fix #6: purge markets past their end_date to prevent unbounded
        # growth of _known_markets.
        now_epoch = int(time.time())
        expired = [
            cid for cid, km in self._known_markets.items() if _is_expired(km.end_date, now_epoch)
        ]
        for cid in expired:
            del self._known_markets[cid]

        self._last_discovery = now
        logger.debug(
            "Rediscovered markets: %d total, %d new, %d purged",
            len(discovered),
            new_count,
            len(expired),
        )

    @property
    def known_market_count(self) -> int:
        """Return the number of known markets in the cache."""
        return len(self._known_markets)
