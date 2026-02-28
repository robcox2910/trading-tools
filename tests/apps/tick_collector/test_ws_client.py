"""Tests for the WebSocket market feed client."""

import json

from trading_tools.apps.tick_collector.ws_client import (
    _build_subscribe_message,
    _is_trade_event,
    _parse_message,
)

_MULTI_TRADE_COUNT = 2

_SAMPLE_TRADE_EVENT = {
    "event_type": "last_trade_price",
    "asset_id": "asset_abc",
    "price": "0.72",
    "size": "15.5",
    "side": "BUY",
    "fee_rate_bps": 200,
    "timestamp": 1700000000000,
}

_SAMPLE_BOOK_EVENT: dict[str, object] = {
    "event_type": "book",
    "asset_id": "asset_abc",
    "bids": [],
    "asks": [],
}

_SAMPLE_PRICE_CHANGE_EVENT = {
    "event_type": "price_change",
    "asset_id": "asset_abc",
    "price": "0.73",
}


class TestParseMessage:
    """Tests for WebSocket message parsing."""

    def test_parse_single_trade_event(self) -> None:
        """Parse a single last_trade_price event dict."""
        raw = json.dumps(_SAMPLE_TRADE_EVENT)
        result = _parse_message(raw)

        assert len(result) == 1
        assert result[0]["event_type"] == "last_trade_price"
        assert result[0]["asset_id"] == "asset_abc"

    def test_parse_array_of_events(self) -> None:
        """Parse an array containing trade and non-trade events."""
        raw = json.dumps([_SAMPLE_TRADE_EVENT, _SAMPLE_BOOK_EVENT])
        result = _parse_message(raw)

        assert len(result) == 1
        assert result[0]["event_type"] == "last_trade_price"

    def test_parse_book_event_ignored(self) -> None:
        """Book events are silently discarded."""
        raw = json.dumps(_SAMPLE_BOOK_EVENT)
        result = _parse_message(raw)

        assert result == []

    def test_parse_price_change_event_ignored(self) -> None:
        """Price change events are silently discarded."""
        raw = json.dumps(_SAMPLE_PRICE_CHANGE_EVENT)
        result = _parse_message(raw)

        assert result == []

    def test_parse_invalid_json(self) -> None:
        """Malformed JSON returns empty list."""
        result = _parse_message("not valid json {{{")

        assert result == []

    def test_parse_empty_string(self) -> None:
        """Empty string returns empty list."""
        result = _parse_message("")

        assert result == []

    def test_parse_bytes_message(self) -> None:
        """Parse a bytes-encoded message."""
        raw = json.dumps(_SAMPLE_TRADE_EVENT).encode()
        result = _parse_message(raw)

        assert len(result) == 1

    def test_parse_array_all_non_trade(self) -> None:
        """Array of non-trade events returns empty list."""
        raw = json.dumps([_SAMPLE_BOOK_EVENT, _SAMPLE_PRICE_CHANGE_EVENT])
        result = _parse_message(raw)

        assert result == []

    def test_parse_multiple_trade_events(self) -> None:
        """Parse an array with multiple trade events."""
        events = [
            {**_SAMPLE_TRADE_EVENT, "asset_id": "asset_1"},
            {**_SAMPLE_TRADE_EVENT, "asset_id": "asset_2"},
        ]
        raw = json.dumps(events)
        result = _parse_message(raw)

        assert len(result) == _MULTI_TRADE_COUNT


class TestIsTradeEvent:
    """Tests for the trade event type check."""

    def test_trade_event_returns_true(self) -> None:
        """Verify last_trade_price events are identified."""
        assert _is_trade_event(_SAMPLE_TRADE_EVENT) is True

    def test_book_event_returns_false(self) -> None:
        """Verify book events are rejected."""
        assert _is_trade_event(_SAMPLE_BOOK_EVENT) is False

    def test_missing_event_type_returns_false(self) -> None:
        """Verify events without event_type are rejected."""
        assert _is_trade_event({"asset_id": "abc"}) is False


class TestBuildSubscribeMessage:
    """Tests for WebSocket subscription message construction."""

    def test_builds_correct_structure(self) -> None:
        """Verify the subscription message has the expected shape."""
        msg = _build_subscribe_message(["asset_1", "asset_2"])

        assert msg["type"] == "market"
        assert msg["assets_ids"] == ["asset_1", "asset_2"]
        assert msg["custom_feature_enabled"] is False

    def test_empty_asset_list(self) -> None:
        """Build a subscription message with no assets."""
        msg = _build_subscribe_message([])

        assert msg["assets_ids"] == []
