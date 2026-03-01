"""Tests for the WebSocket market feed client."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

from trading_tools.apps.tick_collector.ws_client import (
    MarketFeed,
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


class TestUpdateSubscription:
    """Tests for the reconnect-based update_subscription method."""

    @pytest.mark.asyncio
    async def test_update_subscription_closes_ws(self) -> None:
        """Close the active WebSocket so the stream loop reconnects."""
        feed = MarketFeed()
        mock_ws = AsyncMock()
        feed._ws = mock_ws
        await feed.update_subscription(["new_asset"])

        mock_ws.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_update_subscription_sets_reconnect_flag(self) -> None:
        """Set the reconnect flag so stream() skips backoff."""
        feed = MarketFeed()
        feed._ws = AsyncMock()
        await feed.update_subscription(["new_asset"])

        assert feed._reconnect_requested is True

    @pytest.mark.asyncio
    async def test_update_subscription_no_ws_is_noop(self) -> None:
        """Do nothing when no WebSocket connection exists."""
        feed = MarketFeed()

        await feed.update_subscription(["new_asset"])

        assert feed._ws is None
        assert feed._reconnect_requested is False


class TestStreamReconnect:
    """Tests for the stream() reconnect behaviour after subscription update."""

    @pytest.mark.asyncio
    async def test_stream_reconnects_immediately_on_flag(self) -> None:
        """Skip backoff delay when reconnecting for a subscription update."""
        feed = MarketFeed(reconnect_base_delay=5.0)

        call_count = 0

        async def fake_connect_and_listen(
            asset_ids: list[str],  # noqa: ARG001
        ) -> AsyncIterator[dict[str, Any]]:
            """Simulate two connect cycles, triggering reconnect on first."""
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Simulate: caller updates subscription mid-stream
                feed._reconnect_requested = True
                return
                yield  # Make this an async generator  # type: ignore[misc]
            # Second call: stop the loop
            feed._closed = True
            return
            yield  # type: ignore[misc]

        with patch.object(feed, "_connect_and_listen", side_effect=fake_connect_and_listen):
            sleep_calls: list[float] = []
            original_sleep = asyncio.sleep

            async def spy_sleep(delay: float) -> None:
                sleep_calls.append(delay)
                await original_sleep(0)

            with patch("asyncio.sleep", side_effect=spy_sleep):
                async for _event in feed.stream(["asset_1"]):
                    pass  # pragma: no cover

        assert call_count == 2  # noqa: PLR2004
        assert sleep_calls == []

    @pytest.mark.asyncio
    async def test_stream_logs_consecutive_failures(self) -> None:
        """Log the consecutive failure count on repeated connection errors."""
        feed = MarketFeed(reconnect_base_delay=0.0)
        expected_failures = 3

        call_count = 0

        async def failing_connect(
            asset_ids: list[str],  # noqa: ARG001
        ) -> AsyncIterator[dict[str, Any]]:
            """Raise OSError on first calls, then stop the loop."""
            nonlocal call_count
            call_count += 1
            if call_count <= expected_failures:
                msg = f"Connection refused (attempt {call_count})"
                raise OSError(msg)
            feed._closed = True
            return
            yield  # type: ignore[misc]

        with patch.object(feed, "_connect_and_listen", side_effect=failing_connect):
            async for _event in feed.stream(["asset_1"]):
                pass  # pragma: no cover

        assert call_count == expected_failures + 1
