"""Shared parser for raw Polymarket trade dicts into WhaleTrade ORM instances.

The whale monitor collector needs to convert raw API trade dictionaries
into ``WhaleTrade`` objects.
This module centralises that logic so field-name fallbacks and error
handling are defined in exactly one place.
"""

from __future__ import annotations

import logging
from typing import Any

from trading_tools.apps.whale_monitor.models import WhaleTrade
from trading_tools.core.polymarket_fields import (
    extract_asset_id,
    extract_condition_id,
    extract_slug,
)

logger = logging.getLogger(__name__)


def parse_whale_trade(
    raw: dict[str, Any],
    address: str,
    collected_at_ms: int,
) -> WhaleTrade | None:
    """Parse a raw API trade dict into a WhaleTrade ORM instance.

    Handle the field-name inconsistencies across Polymarket API endpoints
    (camelCase vs snake_case) via the shared extraction utilities in
    ``trading_tools.core.polymarket_fields``.

    Args:
        raw: Raw trade dictionary from the Polymarket Data API
            (``/trades`` or ``/activity`` endpoint).
        address: Whale proxy wallet address.
        collected_at_ms: Epoch milliseconds when the trade was fetched.

    Returns:
        A ``WhaleTrade`` instance, or ``None`` if the record is malformed
        (missing required fields or invalid types).

    """
    try:
        return WhaleTrade(
            whale_address=address.lower(),
            transaction_hash=str(raw["transactionHash"]),
            side=str(raw.get("side", "")),
            asset_id=extract_asset_id(raw),
            condition_id=extract_condition_id(raw),
            size=float(raw.get("size", 0)),
            price=float(raw.get("price", 0)),
            timestamp=int(raw.get("timestamp", 0)),
            title=str(raw.get("market", raw.get("title", ""))),
            slug=extract_slug(raw),
            outcome=str(raw.get("outcome", "")),
            outcome_index=int(raw.get("outcome_index", raw.get("outcomeIndex", 0))),
            collected_at=collected_at_ms,
        )
    except (KeyError, ValueError, TypeError):
        logger.debug("Skipping malformed trade record: %s", raw)
        return None
