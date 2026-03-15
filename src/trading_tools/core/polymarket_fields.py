"""Shared field extraction utilities for Polymarket API responses.

The Polymarket Data API and Gamma API return the same logical fields under
inconsistent names (camelCase vs snake_case).  These helpers centralise the
fallback logic so callers do not duplicate ``raw.get("conditionId", raw.get("condition_id", ""))``
patterns throughout the codebase.
"""

from __future__ import annotations

from typing import Any

POLYMARKET_DATA_API_BASE = "https://data-api.polymarket.com"
"""Base URL for the Polymarket Data API (trades, activity, profiles)."""


def extract_condition_id(raw: dict[str, Any]) -> str:
    """Extract the condition ID from a raw API dict.

    Handle both ``conditionId`` (camelCase from the Gamma/CLOB API) and
    ``condition_id`` (snake_case from the Data API) field names.  Try
    the ``or`` fallback pattern to also handle ``None`` values.

    Args:
        raw: Raw dictionary from a Polymarket API response.

    Returns:
        Condition ID string, or ``""`` if neither key is present.

    """
    return str(raw.get("conditionId") or raw.get("condition_id", ""))


def extract_asset_id(raw: dict[str, Any]) -> str:
    """Extract the asset/token ID from a raw API dict.

    Handle ``asset_id`` (Data API) and ``assetId`` (CLOB/Gamma) variants.

    Args:
        raw: Raw dictionary from a Polymarket API response.

    Returns:
        Asset ID string, or ``""`` if neither key is present.

    """
    return str(raw.get("asset_id") or raw.get("assetId", ""))


def extract_slug(raw: dict[str, Any]) -> str:
    """Extract the market slug from a raw API dict.

    Handle ``slug`` and ``market_slug`` field name variants.

    Args:
        raw: Raw dictionary from a Polymarket API response.

    Returns:
        Slug string, or ``""`` if neither key is present.

    """
    return str(raw.get("slug") or raw.get("market_slug", ""))
