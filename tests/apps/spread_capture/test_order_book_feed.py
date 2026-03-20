"""Tests for the WebSocket order book feed."""

import time
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from trading_tools.apps.spread_capture.order_book_feed import (
    OrderBookFeed,
    parse_book_event,
)


class TestParseBookEvent:
    """Test parsing of WebSocket book event messages."""

    def test_parse_valid_book_event(self) -> None:
        """Parse a valid book event with bids and asks."""
        event = {
            "event_type": "book",
            "asset_id": "tok_abc",
            "market": "cond_123",
            "bids": [
                {"price": "0.45", "size": "100"},
                {"price": "0.44", "size": "50"},
            ],
            "asks": [
                {"price": "0.55", "size": "200"},
                {"price": "0.56", "size": "80"},
            ],
        }
        result = parse_book_event(event)
        assert result is not None
        asset_id, book = result
        assert asset_id == "tok_abc"
        assert book.token_id == "tok_abc"
        assert len(book.bids) == 2
        assert len(book.asks) == 2
        assert book.bids[0].price == Decimal("0.45")
        assert book.asks[0].price == Decimal("0.55")
        assert book.spread == Decimal("0.10")
        assert book.midpoint == Decimal("0.50")

    def test_parse_empty_book(self) -> None:
        """Parse a book event with empty bids and asks."""
        event: dict[str, Any] = {
            "event_type": "book",
            "asset_id": "tok_empty",
            "bids": [],
            "asks": [],
        }
        result = parse_book_event(event)
        assert result is not None
        _, book = result
        assert len(book.bids) == 0
        assert len(book.asks) == 0
        assert book.spread == Decimal(0)

    def test_ignore_non_book_event(self) -> None:
        """Return None for non-book event types."""
        event = {"event_type": "last_trade_price", "asset_id": "tok_abc"}
        assert parse_book_event(event) is None

    def test_ignore_missing_asset_id(self) -> None:
        """Return None when asset_id is missing."""
        event: dict[str, Any] = {"event_type": "book", "bids": [], "asks": []}
        assert parse_book_event(event) is None

    def test_parse_handles_invalid_price(self) -> None:
        """Invalid price values are converted to zero."""
        event = {
            "event_type": "book",
            "asset_id": "tok_bad",
            "bids": [{"price": "invalid", "size": "10"}],
            "asks": [],
        }
        result = parse_book_event(event)
        assert result is not None
        _, book = result
        assert book.bids[0].price == Decimal(0)


class TestOrderBookFeedCache:
    """Test the in-memory order book cache."""

    def test_get_book_returns_none_for_unknown_token(self) -> None:
        """Return None for tokens not in the cache."""
        feed = OrderBookFeed()
        assert feed.get_book("unknown_token") is None

    def test_is_stale_returns_true_for_unknown_token(self) -> None:
        """Unknown tokens are always considered stale."""
        feed = OrderBookFeed()
        assert feed.is_stale("unknown_token") is True

    def test_process_message_updates_cache(self) -> None:
        """Process a valid book message and update the cache."""
        feed = OrderBookFeed()
        msg = '[{"event_type": "book", "asset_id": "tok_1", "bids": [{"price": "0.40", "size": "50"}], "asks": [{"price": "0.60", "size": "30"}]}]'
        feed._process_message(msg)

        book = feed.get_book("tok_1")
        assert book is not None
        assert book.bids[0].price == Decimal("0.40")
        assert book.asks[0].price == Decimal("0.60")
        assert feed.event_count == 1
        assert feed.cached_token_count == 1

    def test_process_message_ignores_non_book_events(self) -> None:
        """Non-book events do not update the cache."""
        feed = OrderBookFeed()
        msg = '[{"event_type": "last_trade_price", "asset_id": "tok_1"}]'
        feed._process_message(msg)

        assert feed.get_book("tok_1") is None
        assert feed.event_count == 0

    def test_process_message_handles_malformed_json(self) -> None:
        """Malformed JSON is silently ignored."""
        feed = OrderBookFeed()
        feed._process_message("not valid json{{{")
        assert feed.event_count == 0

    def test_is_stale_respects_timeout(self) -> None:
        """Book becomes stale after the configured timeout."""
        feed = OrderBookFeed(stale_seconds=0.1)
        msg = '[{"event_type": "book", "asset_id": "tok_1", "bids": [], "asks": []}]'
        feed._process_message(msg)

        assert feed.is_stale("tok_1") is False
        time.sleep(0.15)
        assert feed.is_stale("tok_1") is True

    def test_multiple_updates_overwrite_cache(self) -> None:
        """Later book events overwrite earlier ones."""
        feed = OrderBookFeed()
        msg1 = '[{"event_type": "book", "asset_id": "tok_1", "bids": [{"price": "0.40", "size": "50"}], "asks": []}]'
        msg2 = '[{"event_type": "book", "asset_id": "tok_1", "bids": [{"price": "0.45", "size": "60"}], "asks": []}]'
        feed._process_message(msg1)
        feed._process_message(msg2)

        book = feed.get_book("tok_1")
        assert book is not None
        assert book.bids[0].price == Decimal("0.45")
        assert feed.event_count == 2
        assert feed.cached_token_count == 1


@pytest.mark.asyncio
class TestOrderBookFeedLifecycle:
    """Test start/stop and subscription management."""

    async def test_start_creates_task(self) -> None:
        """Starting the feed creates a background task."""
        feed = OrderBookFeed()
        with patch.object(feed, "_run_loop", new_callable=AsyncMock):
            await feed.start(["tok_1", "tok_2"])
            assert feed._task is not None
            assert feed._subscribed_tokens == ["tok_1", "tok_2"]
            await feed.stop()

    async def test_stop_cancels_task(self) -> None:
        """Stopping the feed cancels the background task."""
        feed = OrderBookFeed()
        with patch.object(feed, "_run_loop", new_callable=AsyncMock):
            await feed.start(["tok_1"])
            await feed.stop()
            assert feed._closed is True

    async def test_update_subscription_changes_tokens(self) -> None:
        """Updating subscription replaces the token list."""
        feed = OrderBookFeed()
        feed._subscribed_tokens = ["tok_1"]
        await feed.update_subscription(["tok_2", "tok_3"])
        assert feed._subscribed_tokens == ["tok_2", "tok_3"]
