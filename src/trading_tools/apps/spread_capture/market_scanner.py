"""Market scanner for spread capture opportunities.

Periodically discover active Up/Down markets from series slugs and check
CLOB prices for spread opportunities where the combined cost of buying
both sides is below the configured threshold.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING

import httpx

from trading_tools.apps.spread_capture.models import SpreadOpportunity
from trading_tools.apps.whale_monitor.correlator import parse_asset, parse_time_window
from trading_tools.clients.polymarket.exceptions import PolymarketError
from trading_tools.core.models import ZERO

if TYPE_CHECKING:
    from trading_tools.clients.polymarket.client import PolymarketClient
    from trading_tools.clients.polymarket.models import Market

logger = logging.getLogger(__name__)

_ONE = Decimal(1)


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
        min_spread_margin: Minimum profit margin per token pair.
        max_window_seconds: Maximum market window duration (0 = no limit).
        max_entry_age_pct: Maximum fraction of window elapsed.
        rediscovery_interval: Seconds between market rediscovery calls.

    """

    client: PolymarketClient
    series_slugs: tuple[str, ...]
    max_combined_cost: Decimal
    min_spread_margin: Decimal
    max_window_seconds: int
    max_entry_age_pct: Decimal
    rediscovery_interval: int
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
        opportunities: list[SpreadOpportunity] = []

        for cid in list(self._known_markets):
            if cid in open_cids:
                continue

            opp = await self._evaluate_market(cid, now)
            if opp is not None:
                opportunities.append(opp)

        # Sort by highest margin first
        opportunities.sort(key=lambda o: o.margin, reverse=True)
        return opportunities

    async def _evaluate_market(self, cid: str, now: int) -> SpreadOpportunity | None:
        """Evaluate a single market for spread opportunity.

        Fetch CLOB prices, parse asset and time window, and check
        if the combined cost is below the threshold.

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

        return self._check_spread(market, cid, asset, window[0], window[1], now)

    def _check_spread(
        self,
        market: Market,
        cid: str,
        asset: str,
        window_start_ts: int,
        window_end_ts: int,
        now: int,
    ) -> SpreadOpportunity | None:
        """Check whether a market has a viable spread opportunity.

        Apply window duration, entry age, price, and margin filters.

        Args:
            market: The fetched market with token prices.
            cid: Market condition identifier.
            asset: Parsed asset symbol (e.g. ``"BTC-USD"``).
            window_start_ts: Window start epoch seconds.
            window_end_ts: Window end epoch seconds.
            now: Current epoch seconds.

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
        if up_token.price <= ZERO or down_token.price <= ZERO:
            return None

        combined = up_token.price + down_token.price
        margin = _ONE - combined

        if combined >= self.max_combined_cost:
            return None
        if margin < self.min_spread_margin:
            return None

        return SpreadOpportunity(
            condition_id=cid,
            title=market.question,
            asset=asset,
            up_token_id=up_token.token_id,
            down_token_id=down_token.token_id,
            up_price=up_token.price,
            down_price=down_token.price,
            combined=combined,
            margin=margin,
            window_start_ts=window_start_ts,
            window_end_ts=window_end_ts,
        )

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

        for cid, end_date in discovered:
            if cid not in self._known_markets:
                self._known_markets[cid] = _KnownMarket(condition_id=cid, end_date=end_date)

        self._last_discovery = now
        logger.debug(
            "Rediscovered markets: %d total, %d new",
            len(discovered),
            sum(1 for c, _ in discovered if c not in self._known_markets),
        )

    @property
    def known_market_count(self) -> int:
        """Return the number of known markets in the cache."""
        return len(self._known_markets)
