"""Tests for the WebSocket market feed client."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest
from websockets import ConnectionClosed, frames

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


class TestParseNonDictNonList:
    """Test edge cases for _parse_message with unusual payloads."""

    def test_parse_integer_payload(self) -> None:
        """Integer JSON payload returns empty list."""
        result = _parse_message("42")
        assert result == []

    def test_parse_null_payload(self) -> None:
        """Null JSON payload returns empty list."""
        result = _parse_message("null")
        assert result == []

    def test_parse_string_payload(self) -> None:
        """String JSON payload returns empty list."""
        result = _parse_message('"just a string"')
        assert result == []

    def test_parse_boolean_payload(self) -> None:
        """Boolean JSON payload returns empty list."""
        result = _parse_message("true")
        assert result == []


class TestStreamErrorHandling:
    """Tests for stream() connection error handling and backoff."""

    @pytest.mark.asyncio
    async def test_connection_closed_during_stream(self) -> None:
        """Handle ConnectionClosed and reconnect."""
        feed = MarketFeed(reconnect_base_delay=0.01)
        call_count = 0

        async def fake_connect(
            asset_ids: list[str],  # noqa: ARG001
        ) -> AsyncIterator[dict[str, Any]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionClosed(frames.Close(1000, "normal"), None)
            feed._closed = True
            return
            yield  # type: ignore[misc]

        with patch.object(feed, "_connect_and_listen", side_effect=fake_connect):
            async for _event in feed.stream(["asset_1"]):
                pass  # pragma: no cover

        assert call_count == 2  # noqa: PLR2004

    @pytest.mark.asyncio
    async def test_os_error_during_stream(self) -> None:
        """Handle OSError and reconnect with backoff."""
        feed = MarketFeed(reconnect_base_delay=0.01)
        call_count = 0

        async def fake_connect(
            asset_ids: list[str],  # noqa: ARG001
        ) -> AsyncIterator[dict[str, Any]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OSError("Connection refused")
            feed._closed = True
            return
            yield  # type: ignore[misc]

        with patch.object(feed, "_connect_and_listen", side_effect=fake_connect):
            async for _event in feed.stream(["asset_1"]):
                pass  # pragma: no cover

        assert call_count == 2  # noqa: PLR2004

    @pytest.mark.asyncio
    async def test_backoff_delay_doubles(self) -> None:
        """Verify exponential backoff doubles the delay."""
        feed = MarketFeed(reconnect_base_delay=0.01)
        call_count = 0

        async def fake_connect(
            asset_ids: list[str],  # noqa: ARG001
        ) -> AsyncIterator[dict[str, Any]]:
            nonlocal call_count
            call_count += 1
            if call_count >= 3:  # noqa: PLR2004
                feed._closed = True
            raise OSError("fail")
            yield  # type: ignore[misc]

        sleep_delays: list[float] = []
        original_sleep = asyncio.sleep

        async def spy_sleep(delay: float) -> None:
            sleep_delays.append(delay)
            await original_sleep(0)

        with (
            patch.object(feed, "_connect_and_listen", side_effect=fake_connect),
            patch("asyncio.sleep", side_effect=spy_sleep),
        ):
            async for _event in feed.stream(["asset_1"]):
                pass  # pragma: no cover

        # First delay is base, second is doubled
        assert len(sleep_delays) >= 2  # noqa: PLR2004
        assert sleep_delays[1] > sleep_delays[0]

    @pytest.mark.asyncio
    async def test_connection_closed_when_feed_closed(self) -> None:
        """Exit cleanly when ConnectionClosed happens after close()."""
        feed = MarketFeed(reconnect_base_delay=0.01)

        async def fake_connect(
            asset_ids: list[str],  # noqa: ARG001
        ) -> AsyncIterator[dict[str, Any]]:
            feed._closed = True
            raise ConnectionClosed(frames.Close(1000, "normal"), None)
            yield  # type: ignore[misc]

        event_count = 0
        with patch.object(feed, "_connect_and_listen", side_effect=fake_connect):
            async for _event in feed.stream(["asset_1"]):
                event_count += 1  # pragma: no cover

        assert event_count == 0

    @pytest.mark.asyncio
    async def test_os_error_when_feed_closed(self) -> None:
        """Exit cleanly when OSError happens after close()."""
        feed = MarketFeed(reconnect_base_delay=0.01)

        async def fake_connect(
            asset_ids: list[str],  # noqa: ARG001
        ) -> AsyncIterator[dict[str, Any]]:
            feed._closed = True
            raise OSError("Connection refused")
            yield  # type: ignore[misc]

        event_count = 0
        with patch.object(feed, "_connect_and_listen", side_effect=fake_connect):
            async for _event in feed.stream(["asset_1"]):
                event_count += 1  # pragma: no cover

        assert event_count == 0


class TestClose:
    """Tests for the close() method."""

    @pytest.mark.asyncio
    async def test_close_sets_flag_and_closes_ws(self) -> None:
        """Verify close sets the closed flag and closes WebSocket."""
        feed = MarketFeed()
        mock_ws = AsyncMock()
        feed._ws = mock_ws

        await feed.close()

        assert feed._closed is True
        assert feed._ws is None
        mock_ws.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_close_without_ws(self) -> None:
        """Verify close is safe when no WebSocket exists."""
        feed = MarketFeed()

        await feed.close()

        assert feed._closed is True
        assert feed._ws is None
