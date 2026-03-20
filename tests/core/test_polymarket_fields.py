"""Tests for Polymarket field extraction utilities.

Verify that the shared extraction functions correctly handle the camelCase
vs snake_case naming inconsistencies in Polymarket API responses.
"""

from trading_tools.core.polymarket_fields import (
    POLYMARKET_DATA_API_BASE,
    extract_asset_id,
    extract_condition_id,
    extract_slug,
)


class TestExtractConditionId:
    """Test extract_condition_id with various field naming patterns."""

    def test_camel_case_key(self) -> None:
        """Return value from conditionId (camelCase)."""
        raw = {"conditionId": "0xabc123"}
        assert extract_condition_id(raw) == "0xabc123"

    def test_snake_case_key(self) -> None:
        """Return value from condition_id (snake_case)."""
        raw = {"condition_id": "0xdef456"}
        assert extract_condition_id(raw) == "0xdef456"

    def test_camel_case_preferred_over_snake_case(self) -> None:
        """Prefer conditionId when both keys are present."""
        raw = {"conditionId": "0xcamel", "condition_id": "0xsnake"}
        assert extract_condition_id(raw) == "0xcamel"

    def test_neither_key_returns_empty_string(self) -> None:
        """Return empty string when neither key exists."""
        assert extract_condition_id({}) == ""

    def test_none_value_falls_through(self) -> None:
        """Fall through to snake_case key when camelCase value is None."""
        raw = {"conditionId": None, "condition_id": "0xfallback"}
        assert extract_condition_id(raw) == "0xfallback"

    def test_empty_string_falls_through(self) -> None:
        """Fall through to snake_case key when camelCase value is empty."""
        raw = {"conditionId": "", "condition_id": "0xfallback"}
        assert extract_condition_id(raw) == "0xfallback"


class TestExtractAssetId:
    """Test extract_asset_id with various field naming patterns."""

    def test_snake_case_key(self) -> None:
        """Return value from asset_id (snake_case)."""
        raw = {"asset_id": "token123"}
        assert extract_asset_id(raw) == "token123"

    def test_camel_case_key(self) -> None:
        """Return value from assetId (camelCase)."""
        raw = {"assetId": "token456"}
        assert extract_asset_id(raw) == "token456"

    def test_snake_case_preferred(self) -> None:
        """Prefer asset_id when both keys are present."""
        raw = {"asset_id": "snake", "assetId": "camel"}
        assert extract_asset_id(raw) == "snake"

    def test_neither_key_returns_empty_string(self) -> None:
        """Return empty string when neither key exists."""
        assert extract_asset_id({}) == ""


class TestExtractSlug:
    """Test extract_slug with various field naming patterns."""

    def test_slug_key(self) -> None:
        """Return value from slug key."""
        raw = {"slug": "will-btc-hit-100k"}
        assert extract_slug(raw) == "will-btc-hit-100k"

    def test_market_slug_key(self) -> None:
        """Return value from market_slug key."""
        raw = {"market_slug": "will-eth-hit-5k"}
        assert extract_slug(raw) == "will-eth-hit-5k"

    def test_slug_preferred(self) -> None:
        """Prefer slug when both keys are present."""
        raw = {"slug": "preferred", "market_slug": "fallback"}
        assert extract_slug(raw) == "preferred"

    def test_neither_key_returns_empty_string(self) -> None:
        """Return empty string when neither key exists."""
        assert extract_slug({}) == ""


class TestConstants:
    """Test module-level constants."""

    def test_data_api_base_is_https(self) -> None:
        """Verify the Data API base URL uses HTTPS."""
        assert POLYMARKET_DATA_API_BASE.startswith("https://")

    def test_data_api_base_value(self) -> None:
        """Verify the exact Data API base URL."""
        assert POLYMARKET_DATA_API_BASE == "https://data-api.polymarket.com"
