"""Shared test fixtures and factories for polymarket_bot tests.

Provide reusable helpers to create Market, OrderBook, BotConfig, WebSocket
event, mock client, and mock feed objects used across base_engine, engine,
and live_engine test modules.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from trading_tools.apps.polymarket_bot.models import BotConfig
from trading_tools.clients.polymarket.models import (
    Market,
    MarketToken,
    OrderBook,
    OrderLevel,
)

# Default token / condition IDs — individual test modules may override these
DEFAULT_CONDITION_ID = "cond_test"
DEFAULT_YES_TOKEN_ID = "yes_tok"
DEFAULT_NO_TOKEN_ID = "no_tok"


def make_market(
    condition_id: str = DEFAULT_CONDITION_ID,
    yes_price: Decimal | str = "0.60",
    no_price: Decimal | str = "0.40",
    yes_token_id: str = DEFAULT_YES_TOKEN_ID,
    no_token_id: str = DEFAULT_NO_TOKEN_ID,
    question: str = "Will X happen?",
) -> Market:
    """Create a Market with two tokens for testing.

    Args:
        condition_id: Market condition identifier.
        yes_price: YES token price (string or Decimal).
        no_price: NO token price (string or Decimal).
        yes_token_id: YES token identifier.
        no_token_id: NO token identifier.
        question: Market question text.

    Returns:
        Market instance populated with test data.

    """
    return Market(
        condition_id=condition_id,
        question=question,
        description="Test market",
        tokens=(
            MarketToken(token_id=yes_token_id, outcome="Yes", price=Decimal(str(yes_price))),
            MarketToken(token_id=no_token_id, outcome="No", price=Decimal(str(no_price))),
        ),
        end_date="2026-12-31",
        volume=Decimal(50000),
        liquidity=Decimal(10000),
        active=True,
    )


def make_order_book(
    token_id: str = DEFAULT_YES_TOKEN_ID,
    bid_price: Decimal = Decimal("0.59"),
    ask_price: Decimal = Decimal("0.61"),
    bid_size: Decimal = Decimal(100),
    ask_size: Decimal = Decimal(100),
    extra_bids: tuple[OrderLevel, ...] = (),
    extra_asks: tuple[OrderLevel, ...] = (),
) -> OrderBook:
    """Create an OrderBook with basic bid/ask levels for testing.

    Args:
        token_id: Token identifier the book belongs to.
        bid_price: Top-of-book bid price.
        ask_price: Top-of-book ask price.
        bid_size: Top-of-book bid size.
        ask_size: Top-of-book ask size.
        extra_bids: Additional bid levels appended after the top bid.
        extra_asks: Additional ask levels appended after the top ask.

    Returns:
        OrderBook with computed spread and midpoint.

    """
    bids = (OrderLevel(price=bid_price, size=bid_size), *extra_bids)
    asks = (OrderLevel(price=ask_price, size=ask_size), *extra_asks)
    spread = ask_price - bid_price
    midpoint = (ask_price + bid_price) / 2
    return OrderBook(
        token_id=token_id,
        bids=bids,
        asks=asks,
        spread=spread,
        midpoint=midpoint,
    )


def make_bot_config(
    markets: tuple[str, ...] = (DEFAULT_CONDITION_ID,),
    initial_capital: Decimal = Decimal(1000),
    max_position_pct: Decimal = Decimal("0.25"),
    kelly_fraction: Decimal = Decimal("0.25"),
    max_history: int = 50,
    order_book_refresh_seconds: int = 30,
    series_slugs: tuple[str, ...] = (),
    **kwargs: Any,
) -> BotConfig:
    """Create a BotConfig for testing.

    Args:
        markets: Condition IDs to trade.
        initial_capital: Starting capital.
        max_position_pct: Maximum position size as a fraction of capital.
        kelly_fraction: Kelly criterion scaling factor.
        max_history: Maximum price history length.
        order_book_refresh_seconds: Order book refresh interval.
        series_slugs: Market series slugs for rotation.
        **kwargs: Additional BotConfig fields.

    Returns:
        BotConfig populated with test defaults.

    """
    return BotConfig(
        markets=markets,
        initial_capital=initial_capital,
        max_position_pct=max_position_pct,
        kelly_fraction=kelly_fraction,
        max_history=max_history,
        order_book_refresh_seconds=order_book_refresh_seconds,
        series_slugs=series_slugs,
        **kwargs,
    )


def make_ws_event(
    asset_id: str = DEFAULT_YES_TOKEN_ID,
    price: str = "0.60",
) -> dict[str, Any]:
    """Create a simulated WebSocket trade event.

    Args:
        asset_id: Token ID for the event.
        price: Trade price as string.

    Returns:
        Event dictionary mimicking a ``last_trade_price`` WS message.

    """
    return {"asset_id": asset_id, "price": price}


def mock_polymarket_client(
    market: Market | None = None,
    order_book: OrderBook | None = None,
) -> AsyncMock:
    """Create a mock PolymarketClient with default return values.

    Args:
        market: Market to return from get_market. Defaults to make_market().
        order_book: OrderBook to return from get_order_book. Defaults to make_order_book().

    Returns:
        AsyncMock configured as a PolymarketClient.

    """
    client = AsyncMock()
    client.get_market = AsyncMock(return_value=market or make_market())
    client.get_order_book = AsyncMock(return_value=order_book or make_order_book())
    client.discover_series_markets = AsyncMock(return_value=[])
    return client


def mock_feed(events: list[dict[str, Any]]) -> MagicMock:
    """Create a mock MarketFeed that yields the given events.

    Args:
        events: List of event dicts to yield from stream().

    Returns:
        MagicMock configured as a MarketFeed.

    """
    feed = MagicMock()

    async def mock_stream(asset_ids: list[str]) -> Any:  # noqa: ARG001
        for event in events:
            yield event

    feed.stream = mock_stream
    feed.close = AsyncMock()
    feed.update_subscription = AsyncMock()
    return feed
