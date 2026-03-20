"""Tests for WebSocket-first order book reads in LiveMarketData."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from trading_tools.apps.directional.market_data_live import LiveMarketData
from trading_tools.apps.directional.models import MarketOpportunity
from trading_tools.clients.polymarket.models import OrderBook, OrderLevel
from trading_tools.data.providers.order_book_feed import OrderBookFeed

_HUNDRED = Decimal(100)


def _make_book(token_id: str, bid: str = "0.45", ask: str = "0.55") -> OrderBook:
    """Build a minimal order book for testing.

    Args:
        token_id: Token identifier.
        bid: Best bid price string.
        ask: Best ask price string.

    Returns:
        An ``OrderBook`` with single-level bids and asks.

    """
    return OrderBook(
        token_id=token_id,
        bids=(OrderLevel(price=Decimal(bid), size=_HUNDRED),),
        asks=(OrderLevel(price=Decimal(ask), size=_HUNDRED),),
        spread=Decimal(ask) - Decimal(bid),
        midpoint=(Decimal(bid) + Decimal(ask)) / 2,
    )


@pytest.mark.asyncio
class TestGetOrderBooksWsFallback:
    """Test WS-first, REST-fallback behaviour in get_order_books."""

    async def test_ws_cache_hit_skips_rest(self) -> None:
        """When the WS cache has fresh books, no REST calls are made."""
        client = MagicMock()
        client.get_order_book = AsyncMock()
        candle_provider = MagicMock()

        feed = OrderBookFeed()
        feed._process_message(
            '[{"event_type": "book", "asset_id": "tok_up",'
            ' "bids": [{"price": "0.45", "size": "100"}],'
            ' "asks": [{"price": "0.55", "size": "100"}]}]'
        )
        feed._process_message(
            '[{"event_type": "book", "asset_id": "tok_down",'
            ' "bids": [{"price": "0.45", "size": "100"}],'
            ' "asks": [{"price": "0.55", "size": "100"}]}]'
        )

        data = LiveMarketData(
            client=client,
            candle_provider=candle_provider,
            book_feed=feed,
        )

        result_up, result_down = await data.get_order_books("tok_up", "tok_down")

        assert result_up.token_id == "tok_up"
        assert result_down.token_id == "tok_down"
        client.get_order_book.assert_not_called()

    async def test_stale_cache_falls_back_to_rest(self) -> None:
        """When the WS cache is stale, fall back to REST."""
        rest_book = _make_book("tok_up", bid="0.40", ask="0.60")
        client = MagicMock()
        client.get_order_book = AsyncMock(return_value=rest_book)
        candle_provider = MagicMock()

        # Feed with 0-second staleness so everything is immediately stale
        feed = OrderBookFeed(stale_seconds=0.0)
        feed._process_message(
            '[{"event_type": "book", "asset_id": "tok_up",'
            ' "bids": [{"price": "0.45", "size": "100"}],'
            ' "asks": [{"price": "0.55", "size": "100"}]}]'
        )

        data = LiveMarketData(
            client=client,
            candle_provider=candle_provider,
            book_feed=feed,
        )

        result = await data._get_single_book("tok_up")

        assert result.token_id == "tok_up"
        assert result.bids[0].price == Decimal("0.40")
        client.get_order_book.assert_called_once_with("tok_up")

    async def test_missing_cache_falls_back_to_rest(self) -> None:
        """When the token has no cached book, fall back to REST."""
        rest_book = _make_book("tok_missing")
        client = MagicMock()
        client.get_order_book = AsyncMock(return_value=rest_book)
        candle_provider = MagicMock()

        feed = OrderBookFeed()

        data = LiveMarketData(
            client=client,
            candle_provider=candle_provider,
            book_feed=feed,
        )

        result = await data._get_single_book("tok_missing")

        assert result.token_id == "tok_missing"
        client.get_order_book.assert_called_once_with("tok_missing")

    async def test_no_book_feed_uses_rest(self) -> None:
        """When no book_feed is provided, always use REST."""
        rest_up = _make_book("tok_up")
        rest_down = _make_book("tok_down")
        client = MagicMock()
        client.get_order_book = AsyncMock(side_effect=[rest_up, rest_down])
        candle_provider = MagicMock()

        data = LiveMarketData(
            client=client,
            candle_provider=candle_provider,
        )

        result_up, result_down = await data.get_order_books("tok_up", "tok_down")

        assert result_up.token_id == "tok_up"
        assert result_down.token_id == "tok_down"
        assert client.get_order_book.call_count == 2


@pytest.mark.asyncio
class TestBookFeedSubscriptionSync:
    """Test that get_active_markets syncs the book feed subscription."""

    async def test_sync_updates_subscription(self) -> None:
        """Discovered markets trigger a book feed subscription update."""
        client = MagicMock()
        candle_provider = MagicMock()

        feed = OrderBookFeed()
        feed.update_subscription = AsyncMock()

        data = LiveMarketData(
            client=client,
            candle_provider=candle_provider,
            book_feed=feed,
        )

        markets = [
            MarketOpportunity(
                condition_id="cid_1",
                title="BTC Up/Down 5m",
                asset="BTC-USD",
                up_token_id="tok_up_1",
                down_token_id="tok_down_1",
                window_start_ts=1000,
                window_end_ts=1300,
                up_price=Decimal("0.50"),
                down_price=Decimal("0.50"),
                up_ask_depth=_HUNDRED,
                down_ask_depth=_HUNDRED,
                series_slug="btc-updown-5m",
            ),
        ]

        await data._sync_book_feed(markets)

        feed.update_subscription.assert_called_once()
        call_args = feed.update_subscription.call_args[0][0]
        assert "tok_up_1" in call_args
        assert "tok_down_1" in call_args

    async def test_sync_skips_when_no_feed(self) -> None:
        """No error when book_feed is None."""
        client = MagicMock()
        candle_provider = MagicMock()

        data = LiveMarketData(
            client=client,
            candle_provider=candle_provider,
        )

        # Should not raise
        await data._sync_book_feed([])
