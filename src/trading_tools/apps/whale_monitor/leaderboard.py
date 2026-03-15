"""Discovery of profitable Polymarket traders within a set of target markets.

Two complementary strategies are provided:

``find_profitable_traders`` (market-driven)
    Enumerate every wallet that traded in the target markets via
    ``/trades?market=``, then compute P&L for each from their full history.
    Population: everyone who touched those markets.

``find_leaderboard_traders`` (leaderboard-driven)
    Pull the global Polymarket leaderboard (``/v1/leaderboard``), then filter
    down to traders who participated in the target markets.
    Population: top-ranked traders overall who also traded in those markets.

Both functions accept events as full URLs, slugs, or condition ID hex strings,
and return a ``pandas.DataFrame`` sorted by P&L.

Typical usage::

    import asyncio
    from trading_tools.apps.whale_monitor.leaderboard import find_leaderboard_traders
    from trading_tools.clients.polymarket.client import PolymarketClient


    async def main():
        async with PolymarketClient() as client:
            df = await find_leaderboard_traders(
                client,
                events=["https://polymarket.com/event/btc-updown-5m-1773444300"],
                leaderboard_limit=200,
            )
        print(df.to_string())


    asyncio.run(main())
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, cast

import httpx
import pandas as pd

if TYPE_CHECKING:
    from trading_tools.clients.polymarket.client import PolymarketClient
    from trading_tools.clients.polymarket.models import TraderProfile

logger = logging.getLogger(__name__)

_POLYMARKET_EVENT_PREFIX = "https://polymarket.com/event/"
_MIN_CONDITION_ID_LEN = 10
_TRADES_PER_PAGE = 500
_DEFAULT_CONCURRENCY = 8


# ── Event resolution ─────────────────────────────────────────────────────────


def _resolve_event_input(event: str) -> tuple[str, str]:
    """Classify one event input as a slug or a condition ID.

    Accept any of three common forms:

    - Full event URL -- ``https://polymarket.com/event/<slug>`` -- strips the
      prefix and treats the remainder as a Gamma API slug.
    - Plain slug -- ``btc-updown-5m-1773444300`` -- passed to Gamma as-is.
    - Condition ID -- a ``0x``-prefixed hex string of at least
      ``_MIN_CONDITION_ID_LEN`` characters -- used directly.

    Args:
        event: Raw event string provided by the caller.

    Returns:
        A 2-tuple ``(kind, value)`` where ``kind`` is ``"slug"`` or
        ``"condition_id"``.

    """
    stripped = event.strip()
    if stripped.startswith(_POLYMARKET_EVENT_PREFIX):
        return ("slug", stripped[len(_POLYMARKET_EVENT_PREFIX) :].rstrip("/"))
    if stripped.lower().startswith("0x") and len(stripped) >= _MIN_CONDITION_ID_LEN:
        return ("condition_id", stripped.lower())
    return ("slug", stripped)


async def _resolve_events_to_condition_ids(
    client: PolymarketClient,
    events: list[str],
) -> set[str]:
    """Resolve event inputs to a flat set of condition IDs.

    Query the Gamma API ``/events`` endpoint for each slug.  Condition IDs
    supplied directly are added unchanged.

    Args:
        client: Initialised ``PolymarketClient``.
        events: Raw event strings from the caller.

    Returns:
        Set of lowercase condition ID hex strings covering all requested events.

    """
    condition_ids: set[str] = set()
    slugs: list[str] = []

    for event in events:
        kind, value = _resolve_event_input(event)
        if kind == "condition_id":
            condition_ids.add(value.lower())
        else:
            slugs.append(value)

    if slugs:
        raw_event_lists = await asyncio.gather(
            *(client._gamma.get_events(slug=slug, active=False, limit=50) for slug in slugs),  # type: ignore[reportPrivateUsage]
            return_exceptions=True,
        )
        for slug, result in zip(slugs, raw_event_lists, strict=True):
            if isinstance(result, Exception):
                logger.warning("Failed to resolve slug '%s': %s", slug, result)
                continue
            for event_raw in cast("list[dict[str, object]]", result):
                markets = cast("list[dict[str, object]]", event_raw.get("markets", []))
                for market_raw in markets:
                    cid = str(market_raw.get("conditionId") or market_raw.get("condition_id", ""))
                    if cid:
                        condition_ids.add(cid.lower())
            if not result:  # type: ignore[union-attr]
                logger.warning("No Gamma events found for slug '%s'", slug)

    logger.info(
        "Resolved %d event input(s) to %d condition ID(s)",
        len(events),
        len(condition_ids),
    )
    return condition_ids


# ── Shared helpers ───────────────────────────────────────────────────────────


async def _fetch_market_traders(
    client: PolymarketClient,
    condition_id: str,
) -> dict[str, set[str]]:
    """Return every wallet that traded in a market, keyed by condition ID.

    Paginate ``data-api.polymarket.com/trades?market=<condition_id>`` until
    a short page is received.

    Args:
        client: Initialised ``PolymarketClient``.
        condition_id: Lowercase condition ID hex string.

    Returns:
        Dict mapping proxy wallet address (lowercase) to a set containing this
        condition ID (merged with other markets upstream).

    """
    wallet_to_cids: dict[str, set[str]] = {}
    offset = 0
    while True:
        try:
            page = await client.get_trader_trades_for_market(
                condition_id, limit=_TRADES_PER_PAGE, offset=offset
            )
        except (httpx.HTTPError, KeyError, ValueError):
            logger.warning(
                "Failed to fetch trades for market %s at offset %d",
                condition_id[:20],
                offset,
            )
            break
        for trade in page:
            wallet = str(trade.get("proxyWallet", "")).lower()
            if wallet:
                wallet_to_cids.setdefault(wallet, set()).add(condition_id)
        if len(page) < _TRADES_PER_PAGE:
            break
        offset += _TRADES_PER_PAGE
    logger.info("Market %s: %d unique traders", condition_id[:20], len(wallet_to_cids))
    return wallet_to_cids


def _compute_pnl(trades: list[dict[str, object]]) -> tuple[float, float, int]:
    """Compute gross P&L, total volume, and trade count from a raw trade list.

    A BUY of ``size`` shares at ``price`` is an outflow of ``size * price``
    USDC; a SELL is an inflow.  Gross P&L is inflows minus outflows.
    This is pre-fee (Polymarket taker fees are approximately 2% of notional).

    Args:
        trades: Raw trade dicts from ``data-api.polymarket.com/trades?user=``.

    Returns:
        3-tuple of ``(gross_pnl, total_volume, trade_count)``.

    """
    pnl = 0.0
    volume = 0.0
    for trade in trades:
        size = float(trade.get("size") or 0)  # type: ignore[arg-type]
        price = float(trade.get("price") or 0)  # type: ignore[arg-type]
        notional = size * price
        volume += notional
        if str(trade.get("side", "")).upper() == "SELL":
            pnl += notional
        else:
            pnl -= notional
    return pnl, volume, len(trades)


async def _fetch_wallet_metrics(
    client: PolymarketClient,
    wallet: str,
    target_cids: set[str],
    matched_cids_hint: set[str],
    max_pages: int,
) -> dict[str, object] | None:
    """Fetch trade history for one wallet and compute P&L metrics.

    Args:
        client: Initialised ``PolymarketClient``.
        wallet: Proxy wallet address (lowercase).
        target_cids: Full set of condition IDs the caller asked about.
        matched_cids_hint: Condition IDs already confirmed for this wallet
            (from the market enumeration step); avoids re-scanning for them.
        max_pages: Maximum pages of 500 trades to scan.

    Returns:
        Metrics dict, or ``None`` if the fetch fails entirely.

    """
    all_trades: list[dict[str, object]] = []
    matched_cids: set[str] = set(matched_cids_hint)

    for page_num in range(max_pages):
        try:
            page = await client.get_trader_trades(
                wallet,
                limit=_TRADES_PER_PAGE,
                offset=page_num * _TRADES_PER_PAGE,
            )
        except (httpx.HTTPError, KeyError, ValueError):
            logger.debug("Trade fetch failed for wallet %s page %d", wallet[:10], page_num)
            break
        all_trades.extend(page)
        for trade in page:
            cid = str(trade.get("conditionId") or trade.get("condition_id", "")).lower()
            if cid in target_cids:
                matched_cids.add(cid)
        if len(page) < _TRADES_PER_PAGE:
            break

    if not all_trades:
        return None

    pnl, volume, count = _compute_pnl(all_trades)
    unique_markets = len(
        {
            str(t.get("conditionId") or t.get("condition_id", ""))
            for t in all_trades
            if t.get("conditionId") or t.get("condition_id")
        }
    )

    name = ""
    for trade in all_trades:
        candidate = str(trade.get("pseudonym") or trade.get("name", ""))
        if candidate and not candidate.startswith("0x"):
            name = candidate
            break

    return {
        "proxy_wallet": wallet,
        "name": name,
        "pnl": round(pnl, 2),
        "volume": round(volume, 2),
        "roi": round(pnl / volume, 4) if volume > 0 else 0.0,
        "total_markets_traded": unique_markets,
        "total_trades": count,
        "matched_market_count": len(matched_cids),
        "matched_condition_ids": sorted(matched_cids),
    }


def _build_dataframe(rows: list[dict[str, object]]) -> pd.DataFrame:
    """Build a sorted DataFrame from a list of wallet metric dicts.

    Args:
        rows: List of metric dicts from ``_fetch_wallet_metrics``.

    Returns:
        ``pandas.DataFrame`` sorted by ``pnl`` descending, index reset.

    """
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    return df.sort_values("pnl", ascending=False).reset_index(drop=True)


# ── Public API ───────────────────────────────────────────────────────────────


async def find_profitable_traders(
    client: PolymarketClient,
    events: list[str],
    *,
    max_history_pages: int = 2,
    concurrency: int = _DEFAULT_CONCURRENCY,
) -> pd.DataFrame:
    """Discover and rank every trader who participated in the target events.

    Enumerate wallets bottom-up from the markets (no prior knowledge of
    addresses needed), then compute P&L from their full trade history.
    The population is all traders who touched at least one target market.

    Args:
        client: Initialised ``PolymarketClient`` (no auth needed).
        events: One or more Polymarket event identifiers (full URLs, slugs,
            or condition ID hex strings).  A wallet is included if it has
            at least one trade in any listed event.
        max_history_pages: Maximum pages of 500 trades to fetch per wallet
            for P&L computation.
        concurrency: Parallel wallet metric fetches.  Keep at or below 10
            to avoid soft-throttling by the Data API.

    Returns:
        ``pandas.DataFrame`` sorted by ``pnl`` descending.  Columns:
        ``proxy_wallet``, ``name``, ``pnl``, ``roi``, ``volume``,
        ``total_markets_traded``, ``total_trades``, ``matched_market_count``,
        ``matched_condition_ids``.

    """
    target_cids = await _resolve_events_to_condition_ids(client, events)
    if not target_cids:
        logger.warning("No condition IDs resolved — returning empty DataFrame")
        return pd.DataFrame()

    logger.info("Enumerating traders across %d market(s)...", len(target_cids))
    market_results = await asyncio.gather(
        *(_fetch_market_traders(client, cid) for cid in target_cids)
    )

    wallet_to_cids: dict[str, set[str]] = {}
    for result in market_results:
        for wallet, cids in result.items():
            wallet_to_cids.setdefault(wallet, set()).update(cids)

    logger.info("Found %d unique traders across target markets", len(wallet_to_cids))

    semaphore = asyncio.Semaphore(concurrency)

    async def _guarded(wallet: str, hint: set[str]) -> dict[str, object] | None:
        """Fetch metrics for one wallet under the shared concurrency limit."""
        async with semaphore:
            return await _fetch_wallet_metrics(client, wallet, target_cids, hint, max_history_pages)

    all_metrics = await asyncio.gather(*(_guarded(w, cids) for w, cids in wallet_to_cids.items()))

    rows = [m for m in all_metrics if m is not None]
    logger.info("Returning %d trader rows", len(rows))
    return _build_dataframe(rows)


async def find_leaderboard_traders(
    client: PolymarketClient,
    events: list[str],
    *,
    leaderboard_limit: int = 200,
    leaderboard_time_period: str = "ALL",
    leaderboard_order_by: str = "PNL",
    leaderboard_category: str = "OVERALL",
    max_history_pages: int = 2,
    concurrency: int = _DEFAULT_CONCURRENCY,
) -> pd.DataFrame:
    """Filter the global Polymarket leaderboard to traders in the target events.

    Pull the top ``leaderboard_limit`` traders from the official Polymarket
    leaderboard (``/v1/leaderboard``), then check each wallet's trade history
    to find those who participated in at least one of the target events.
    The leaderboard P&L and volume come from Polymarket's own calculations;
    per-trade metrics are also computed locally from the trade history scan.

    Args:
        client: Initialised ``PolymarketClient`` (no auth needed).
        events: One or more Polymarket event identifiers (full URLs, slugs,
            or condition ID hex strings).
        leaderboard_limit: Number of traders to pull from the global leaderboard.
            Higher values increase coverage at the cost of more API calls.
        leaderboard_time_period: Leaderboard time window.  One of
            ``"DAY"``, ``"WEEK"``, ``"MONTH"``, ``"ALL"``.
        leaderboard_order_by: Leaderboard ranking field.  ``"PNL"`` or
            ``"VOL"``.
        leaderboard_category: Market category.  ``"OVERALL"`` or a specific
            category such as ``"CRYPTO"``, ``"POLITICS"``, ``"SPORTS"``.
        max_history_pages: Maximum pages of 500 trades to fetch per wallet
            to confirm participation and compute local P&L.
        concurrency: Parallel wallet checks.

    Returns:
        ``pandas.DataFrame`` sorted by ``leaderboard_pnl`` descending.
        Columns: ``rank``, ``proxy_wallet``, ``name``, ``leaderboard_pnl``,
        ``leaderboard_volume``, ``pnl`` (computed locally), ``roi``,
        ``volume``, ``total_markets_traded``, ``total_trades``,
        ``matched_market_count``, ``matched_condition_ids``.

    """
    target_cids = await _resolve_events_to_condition_ids(client, events)
    if not target_cids:
        logger.warning("No condition IDs resolved — returning empty DataFrame")
        return pd.DataFrame()

    logger.info(
        "Fetching leaderboard: top %d traders (%s / %s / %s)",
        leaderboard_limit,
        leaderboard_time_period,
        leaderboard_order_by,
        leaderboard_category,
    )
    profiles = await client.get_leaderboard(
        limit=leaderboard_limit,
        time_period=leaderboard_time_period,
        order_by=leaderboard_order_by,
        category=leaderboard_category,
    )
    logger.info("Leaderboard: %d profiles fetched", len(profiles))

    semaphore = asyncio.Semaphore(concurrency)

    async def _check(profile: TraderProfile) -> dict[str, object] | None:
        """Check one leaderboard profile under the shared concurrency limit."""
        async with semaphore:
            metrics = await _fetch_wallet_metrics(
                client, profile.proxy_wallet, target_cids, set(), max_history_pages
            )
        if metrics is None or metrics["matched_market_count"] == 0:
            return None
        return {
            "rank": profile.rank,
            "leaderboard_pnl": profile.pnl,
            "leaderboard_volume": profile.volume,
            **metrics,
        }

    results = await asyncio.gather(*(_check(p) for p in profiles))
    rows = [r for r in results if r is not None]

    logger.info(
        "%d of %d leaderboard traders participated in target events",
        len(rows),
        len(profiles),
    )

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    return df.sort_values("leaderboard_pnl", ascending=False).reset_index(drop=True)
