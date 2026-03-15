"""Trade enrichment from Polymarket Gamma market metadata.

Enrich a list of raw ``WhaleTrade`` records with market-level context fetched
from the Gamma API via ``PolymarketClient.get_market_info``.  Each unique
condition ID is resolved at most once; subsequent lookups are served from an
in-memory cache that the caller can hold across multiple invocations.

Typical usage::

    from trading_tools.apps.whale_monitor.enricher import enrich_trades
    from trading_tools.clients.polymarket.client import PolymarketClient

    cache: dict = {}
    async with PolymarketClient() as client:
        enriched = await enrich_trades(client, trades, cache=cache)

"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from trading_tools.apps.whale_monitor.models import WhaleTrade
    from trading_tools.clients.polymarket.client import PolymarketClient

logger = logging.getLogger(__name__)


# ── Outcome structure enum ────────────────────────────────────────────────────


class OutcomeStructure(Enum):
    """Classification of a market's possible outcomes.

    Describe the shape of the outcome space so consumers can handle each
    type uniformly rather than comparing raw strings.

    Attributes:
        YES_NO: Binary market with ``Yes`` and ``No`` outcomes.
        UP_DOWN: Binary market with ``Up`` and ``Down`` outcomes (typical for
            price-direction series markets).
        MULTI: Market with three or more distinct outcomes.
        UNKNOWN: Outcomes could not be determined from the API response.

    """

    YES_NO = "YES_NO"
    UP_DOWN = "UP_DOWN"
    MULTI = "MULTI"
    UNKNOWN = "UNKNOWN"


# ── Market metadata ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MarketMetadata:
    """Enriched market-level context fetched from the Gamma API.

    Contain the fields extracted from a ``get_market_info`` response that
    are not available on a ``WhaleTrade`` record.  Instances are cached by
    condition ID so the API is called at most once per market.

    Attributes:
        condition_id: Market condition identifier (lowercase hex).
        category: Market category string (e.g. ``"sports"``,
            ``"US-current-affairs"``).  Empty string when the API returns
            ``null`` for this field.
        is_recurring: ``True`` when the market belongs to a time-series
            (e.g. ``btc-up-or-down-5m``).
        recurrence: Recurrence interval string (e.g. ``"5m"``, ``"15m"``,
            ``"1h"``) for recurring markets; empty string otherwise.
        outcome_structure: Enum describing the shape of the outcome space.
        close_datetime: UTC datetime when the market closes or closed.
            ``None`` when the API returns no end date.
        winning_outcome: The label of the winning outcome (e.g. ``"Yes"``,
            ``"Up"``) for resolved markets, or ``None`` if the market is
            still open.

    """

    condition_id: str
    category: str
    is_recurring: bool
    recurrence: str
    outcome_structure: OutcomeStructure
    close_datetime: datetime | None
    winning_outcome: str | None


# ── Enriched trade ────────────────────────────────────────────────────────────


@dataclass
class EnrichedTrade:
    """A ``WhaleTrade`` record annotated with Gamma market metadata.

    Combine a raw trade with the ``MarketMetadata`` fetched for its market,
    surfacing fields that are not available on the underlying ORM model.

    P&L is computed per-trade assuming each trade is viewed independently
    against the market's resolution outcome:

    - **BUY on winning outcome**: earned ``size * (1 - price)``
    - **BUY on losing outcome**: lost ``size * price``
    - **SELL on winning outcome**: foregone ``size * (1 - price)`` by selling
      early (negative P&L vs holding)
    - **SELL on losing outcome**: saved ``size * price`` by selling before
      resolution (positive P&L vs holding)

    The unified formula is ``direction * size * (resolution_value - price)``
    where ``direction`` is ``+1`` for BUY and ``-1`` for SELL, and
    ``resolution_value`` is ``1.0`` for the winning outcome, ``0.0`` for
    the losing outcome.

    Attributes:
        trade: Original ``WhaleTrade`` ORM instance.
        category: Market category from the Gamma API.
        is_recurring: Whether this market is part of a recurring series.
        recurrence: Recurrence interval (e.g. ``"5m"``); empty for
            non-recurring markets.
        outcome_structure: Classification of the market's outcome space.
        close_datetime: UTC datetime when the market closes or closed.
        winning_outcome: The winning outcome label for resolved markets, or
            ``None`` when the market has not yet resolved.
        trade_pnl: Realised P&L for this trade in USDC, or ``None`` when
            the market has not yet resolved.
        is_active: ``True`` when the market has not yet resolved and the
            position may still move; ``False`` once a winner is known.

    """

    trade: WhaleTrade
    category: str
    is_recurring: bool
    recurrence: str
    outcome_structure: OutcomeStructure
    close_datetime: datetime | None
    winning_outcome: str | None
    trade_pnl: float | None
    is_active: bool

    def __str__(self) -> str:
        """Return a concise human-readable summary of this enriched trade.

        Extends the underlying ``WhaleTrade`` summary with category, recurrence,
        outcome structure, P&L, and active status.

        Format:
            ``<WhaleTrade str> | <category> | <outcome_structure> [<recurrence>] | pnl=<pnl> | active=<active>``

        Returns:
            Single-line string representation.

        """
        recurrence_part = f" [{self.recurrence}]" if self.recurrence else ""
        pnl_part = f"{self.trade_pnl:.4f}" if self.trade_pnl is not None else "n/a"
        return (
            f"{self.trade}"
            f" | {self.category}"
            f" | {self.outcome_structure.value}{recurrence_part}"
            f" | pnl={pnl_part}"
            f" | active={self.is_active}"
        )


# ── Internal helpers ──────────────────────────────────────────────────────────


def _parse_outcome_structure(raw_outcomes: object) -> OutcomeStructure:
    """Classify the outcome structure from the raw Gamma ``outcomes`` field.

    The Gamma API returns ``outcomes`` as a JSON-encoded string such as
    ``'["Yes", "No"]'``.  Parse it and map the values to an
    ``OutcomeStructure`` enum member.

    Args:
        raw_outcomes: The ``outcomes`` value from the market dict —
            typically a JSON string or ``None``.

    Returns:
        The matching ``OutcomeStructure`` member, or ``UNKNOWN`` when the
        field is absent, malformed, or contains unrecognised values.

    """
    if not raw_outcomes:
        return OutcomeStructure.UNKNOWN
    try:
        outcomes: list[str] = json.loads(str(raw_outcomes))
    except (ValueError, TypeError):
        return OutcomeStructure.UNKNOWN

    normalised = {o.strip().lower() for o in outcomes}
    if normalised == {"yes", "no"}:
        return OutcomeStructure.YES_NO
    if normalised == {"up", "down"}:
        return OutcomeStructure.UP_DOWN
    if len(outcomes) >= 2:  # noqa: PLR2004
        return OutcomeStructure.MULTI
    return OutcomeStructure.UNKNOWN


def _parse_close_datetime(raw: object) -> datetime | None:
    """Parse a close datetime string from the Gamma API into a UTC datetime.

    Try ``closedTime`` first (set for resolved markets), then ``endDate``
    (the scheduled end for open or future markets).  Both arrive as ISO-8601
    strings, sometimes with timezone offsets and sometimes with a space
    instead of ``T`` as the date/time separator.

    Args:
        raw: The raw datetime string from the API, or ``None``.

    Returns:
        A timezone-aware ``datetime`` in UTC, or ``None`` if parsing fails.

    """
    if not raw:
        return None
    s = str(raw).strip().replace(" ", "T")
    # Normalise short UTC offset ``+00`` → ``+0000`` so ``%z`` can parse it.
    # Python's strptime %z requires ±HHMM or ±HH:MM, not the bare ±HH form.
    s = re.sub(r"([+-]\d{2})$", r"\g<1>00", s)
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            dt = datetime.strptime(s, fmt)  # noqa: DTZ007
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt.astimezone(UTC)
        except ValueError:
            continue
    logger.debug("Could not parse close_datetime: %r", raw)
    return None


# Slugs of Gamma event tags that map to canonical top-level category labels.
# Newer recurring markets (e.g. BTC Up/Down 5m) have no ``category`` field;
# their category is encoded in the event ``tags`` array instead.
_CATEGORY_TAG_SLUGS: dict[str, str] = {
    "crypto": "Crypto",
    "crypto-prices": "Crypto",
    "sports": "Sports",
    "politics": "Politics",
    "business": "Business",
    "entertainment": "Entertainment",
    "pop-culture": "Pop Culture",
    "science": "Science",
    "technology": "Technology",
    "esports": "Esports",
    "us-current-affairs": "US Politics",
    "ukraine-russia": "Geopolitics",
}


def _infer_category_from_tags(tags: list[dict[str, object]]) -> str:
    """Infer a category label from a Gamma event ``tags`` array.

    Check each tag's ``slug`` against the known category slug mapping first.
    If no known slug matches, fall back to the label of the first tag that
    has a ``publishedAt`` timestamp — these are curated content tags rather
    than internal operational tags such as ``"Recurring"`` or ``"Hide From New"``.

    Args:
        tags: List of tag dicts from the Gamma ``events[].tags`` field.

    Returns:
        Category label string, or empty string when no match is found.

    """
    for tag in tags:
        slug = str(tag.get("slug") or "").lower()
        if slug in _CATEGORY_TAG_SLUGS:
            return _CATEGORY_TAG_SLUGS[slug]

    # Fallback: first tag with a publishedAt timestamp is a curated content tag
    for tag in tags:
        if tag.get("publishedAt"):
            return str(tag.get("label") or "")

    return ""


def _extract_metadata(condition_id: str, data: dict[str, object]) -> MarketMetadata:
    """Build a ``MarketMetadata`` from a raw Gamma market dict.

    Resolution order for ``category``:

    1. Market-level ``category`` field.
    2. Event-level ``category`` field.
    3. Event-level ``tags`` array — matched against ``_CATEGORY_TAG_SLUGS``,
       then first tag with a ``publishedAt`` timestamp.  This covers newer
       recurring markets (e.g. BTC Up/Down 5m) that omit the ``category``
       field entirely.

    Resolution order for ``close_datetime`` (resolution time):

    1. Market-level ``closedTime`` — actual resolution timestamp for resolved
       markets.
    2. Event-level ``closedTime`` — same value for most markets; acts as a
       safety net when the market-level field is absent.
    3. Market-level ``endDate`` — scheduled end time for open markets.

    Args:
        condition_id: The condition ID used to request this market.
        data: Raw market dict from ``PolymarketClient.get_market_info``.

    Returns:
        A populated ``MarketMetadata`` instance.

    """
    category = str(data.get("category") or "")

    raw_events = data.get("events")
    events: list[dict[str, object]] = list(raw_events) if isinstance(raw_events, list) else []  # type: ignore[arg-type]
    event: dict[str, object] = events[0] if events else {}

    # 2. Event-level category field
    if not category:
        category = str(event.get("category") or "")

    # 3. Infer from event tags (covers newer recurring markets)
    if not category:
        raw_tags = event.get("tags")
        tags: list[dict[str, object]] = list(raw_tags) if isinstance(raw_tags, list) else []  # type: ignore[arg-type]
        category = _infer_category_from_tags(tags)

    raw_series = event.get("series")
    series_list: list[dict[str, object]] = list(raw_series) if isinstance(raw_series, list) else []  # type: ignore[arg-type]
    is_recurring = bool(series_list)
    recurrence = str(series_list[0].get("recurrence", "")) if series_list else ""

    outcome_structure = _parse_outcome_structure(data.get("outcomes"))

    # closedTime = actual resolution time; endDate = scheduled end for open markets.
    # Check market level first, then event level, then fall back to endDate.
    close_datetime = _parse_close_datetime(
        data.get("closedTime") or event.get("closedTime") or data.get("endDate")
    )

    # The Gamma API ``winner`` field is unreliable (often None even for closed
    # markets).  Fall back to inferring the winner from ``outcomePrices``:
    # parse the JSON array and treat the outcome whose price is >= 0.99 as the
    # winner.  This matches how Polymarket resolves binary markets (winning
    # token converges to $1, losing token to $0).
    raw_winner = data.get("winner")
    winning_outcome: str | None = str(raw_winner).strip() if raw_winner else None

    if winning_outcome is None:
        raw_outcomes = data.get("outcomes")
        raw_prices = data.get("outcomePrices")
        try:
            outcomes_list: list[str] = (
                json.loads(raw_outcomes) if isinstance(raw_outcomes, str) else []
            )
            raw_prices_str: str = raw_prices if isinstance(raw_prices, str) else "[]"
            prices_list: list[float] = [float(str(p)) for p in json.loads(raw_prices_str)]
            resolution_threshold = 0.99
            for outcome_label, price in zip(outcomes_list, prices_list, strict=False):
                if price >= resolution_threshold:
                    winning_outcome = outcome_label
                    break
        except Exception:  # noqa: S110
            pass

    return MarketMetadata(
        condition_id=condition_id,
        category=category,
        is_recurring=is_recurring,
        recurrence=recurrence,
        outcome_structure=outcome_structure,
        close_datetime=close_datetime,
        winning_outcome=winning_outcome,
    )


def _compute_trade_pnl(trade: WhaleTrade, winning_outcome: str | None) -> float | None:
    """Compute the realised P&L for a single trade given the winning outcome.

    Each trade is evaluated independently: a BUY of the winning outcome
    earned ``size * (1 - price)`` USDC; a BUY of the losing outcome lost
    ``size * price``.  For SELL trades the logic is symmetric — selling the
    winning outcome early foregoes value (negative P&L), while selling the
    losing outcome before resolution is profitable.

    The unified formula is::

        direction * size * (resolution_value - price)

    where ``direction`` is ``+1`` for BUY and ``-1`` for SELL, and
    ``resolution_value`` is ``1.0`` when the trade's outcome matches the
    winner, ``0.0`` otherwise.

    Args:
        trade: The raw trade record to evaluate.
        winning_outcome: The label of the winning outcome (e.g. ``"Up"``),
            or ``None`` when the market has not yet resolved.

    Returns:
        Realised P&L in USDC rounded to 4 decimal places, or ``None`` when
        the market is unresolved.

    """
    if winning_outcome is None:
        return None
    direction = 1.0 if trade.side.upper() == "BUY" else -1.0
    resolution_value = (
        1.0 if trade.outcome.strip().lower() == winning_outcome.strip().lower() else 0.0
    )
    return round(direction * float(trade.size) * (resolution_value - float(trade.price)), 4)


# ── Public API ────────────────────────────────────────────────────────────────


async def enrich_trades(
    client: PolymarketClient,
    trades: list[WhaleTrade],
    *,
    cache: dict[str, MarketMetadata] | None = None,
) -> list[EnrichedTrade]:
    """Enrich a list of trades with Gamma market metadata.

    For each unique ``condition_id`` in ``trades``, fetch market metadata
    from the Gamma API exactly once.  Subsequent lookups for the same
    condition ID are served from ``cache`` with no network call.

    Pass the same ``cache`` dict across multiple calls to share it across
    different traders — this is the primary mechanism for avoiding redundant
    API requests when enriching trades for many wallets in sequence.

    Args:
        client: Initialised ``PolymarketClient`` (no auth required).
        trades: Raw ``WhaleTrade`` instances to enrich.
        cache: Optional shared cache mapping condition IDs to their
            ``MarketMetadata``.  A new empty dict is created if omitted.
            Mutated in-place so callers can inspect or reuse it.

    Returns:
        List of ``EnrichedTrade`` instances in the same order as ``trades``.
        Trades whose market metadata could not be fetched are included with
        empty/default enrichment fields so the list length always matches.

    """
    if cache is None:
        cache = {}

    # Determine which condition IDs still need fetching
    unique_cids = {t.condition_id for t in trades} - cache.keys()

    for cid in unique_cids:
        try:
            _, data = await client.get_market_info(cid)
            meta = _extract_metadata(cid, data)

            # For newer recurring markets the ``category`` field is absent on both
            # the market and the embedded event dict.  The full event (fetched from
            # the /events endpoint) carries a rich ``tags`` array that encodes the
            # category.  Perform a targeted follow-up fetch only when needed.
            if not meta.category:
                raw_events = data.get("events")
                events_list = list(raw_events) if isinstance(raw_events, list) else []  # type: ignore[arg-type]
                event_slug = str(events_list[0].get("slug") or "") if events_list else ""  # type: ignore[union-attr]
                if event_slug:
                    try:
                        full_events: list[dict[str, object]] = await client._gamma.get_events(  # type: ignore[attr-defined]
                            slug=event_slug, active=False, limit=1
                        )
                        if full_events:
                            raw_tags = full_events[0].get("tags")
                            raw_tags_list: list[object] = (
                                cast("list[object]", raw_tags) if isinstance(raw_tags, list) else []
                            )
                            tags: list[dict[str, object]] = [
                                t for t in raw_tags_list if isinstance(t, dict)
                            ]
                            inferred = _infer_category_from_tags(tags)
                            if inferred:
                                # Rebuild metadata with the resolved category
                                meta = MarketMetadata(
                                    condition_id=meta.condition_id,
                                    category=inferred,
                                    is_recurring=meta.is_recurring,
                                    recurrence=meta.recurrence,
                                    outcome_structure=meta.outcome_structure,
                                    close_datetime=meta.close_datetime,
                                    winning_outcome=meta.winning_outcome,
                                )
                    except Exception:
                        logger.debug("Could not fetch event tags for slug %r", event_slug)

            cache[cid] = meta
        except Exception:
            logger.warning("Failed to fetch market info for %s", cid[:20])
            cache[cid] = MarketMetadata(
                condition_id=cid,
                category="",
                is_recurring=False,
                recurrence="",
                outcome_structure=OutcomeStructure.UNKNOWN,
                close_datetime=None,
                winning_outcome=None,
            )

    enriched: list[EnrichedTrade] = []
    for trade in trades:
        meta = cache[trade.condition_id]
        pnl = _compute_trade_pnl(trade, meta.winning_outcome)
        enriched.append(
            EnrichedTrade(
                trade=trade,
                category=meta.category,
                is_recurring=meta.is_recurring,
                recurrence=meta.recurrence,
                outcome_structure=meta.outcome_structure,
                close_datetime=meta.close_datetime,
                winning_outcome=meta.winning_outcome,
                trade_pnl=pnl,
                is_active=meta.winning_outcome is None,
            )
        )

    return enriched
