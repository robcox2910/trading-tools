"""Tests for PaperTradingEngine WebSocket-driven event loop."""

import logging
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trading_tools.apps.polymarket_bot.engine import PaperTradingEngine
from trading_tools.apps.polymarket_bot.models import (
    BotConfig,
    PaperTradingResult,
)
from trading_tools.apps.polymarket_bot.strategies.mean_reversion import (
    PMMeanReversionStrategy,
)
from trading_tools.clients.polymarket.exceptions import PolymarketAPIError
from trading_tools.clients.polymarket.models import (
    Market,
    MarketToken,
    OrderBook,
    OrderLevel,
)
from trading_tools.core.models import Side

_CONDITION_ID = "cond_engine_test"
_YES_TOKEN_ID = "yes_tok"
_NO_TOKEN_ID = "no_tok"
_INITIAL_CAPITAL = Decimal(1000)


def _make_market(yes_price: str = "0.60", no_price: str = "0.40") -> Market:
    """Create a Market with given prices.

    Args:
        yes_price: YES token price as string.
        no_price: NO token price as string.

    Returns:
        Market instance for testing.

    """
    return Market(
        condition_id=_CONDITION_ID,
        question="Will BTC reach $200K?",
        description="Test market",
        tokens=(
            MarketToken(token_id=_YES_TOKEN_ID, outcome="Yes", price=Decimal(yes_price)),
            MarketToken(token_id=_NO_TOKEN_ID, outcome="No", price=Decimal(no_price)),
        ),
        end_date="2026-12-31",
        volume=Decimal(50000),
        liquidity=Decimal(10000),
        active=True,
    )


def _make_order_book() -> OrderBook:
    """Create a sample order book.

    Returns:
        OrderBook with sample bid and ask levels.

    """
    return OrderBook(
        token_id=_YES_TOKEN_ID,
        bids=(
            OrderLevel(price=Decimal("0.59"), size=Decimal(100)),
            OrderLevel(price=Decimal("0.58"), size=Decimal(200)),
        ),
        asks=(
            OrderLevel(price=Decimal("0.61"), size=Decimal(150)),
            OrderLevel(price=Decimal("0.62"), size=Decimal(50)),
        ),
        spread=Decimal("0.02"),
        midpoint=Decimal("0.60"),
    )


def _make_config() -> BotConfig:
    """Create a BotConfig for testing.

    Returns:
        BotConfig with the test condition_id.

    """
    return BotConfig(
        order_book_refresh_seconds=30,
        initial_capital=_INITIAL_CAPITAL,
        max_position_pct=Decimal("0.1"),
        kelly_fraction=Decimal("0.25"),
        max_history=100,
        markets=(_CONDITION_ID,),
    )


def _mock_client(market: Market | None = None, order_book: OrderBook | None = None) -> AsyncMock:
    """Create a mock PolymarketClient.

    Args:
        market: Market to return from get_market.
        order_book: OrderBook to return from get_order_book.

    Returns:
        AsyncMock configured as a PolymarketClient.

    """
    client = AsyncMock()
    client.get_market = AsyncMock(return_value=market or _make_market())
    client.get_order_book = AsyncMock(return_value=order_book or _make_order_book())
    return client


def _make_ws_event(asset_id: str = _YES_TOKEN_ID, price: str = "0.60") -> dict[str, Any]:
    """Create a WebSocket trade event.

    Args:
        asset_id: Token ID for the event.
        price: Trade price as string.

    Returns:
        Event dictionary mimicking a ``last_trade_price`` WS message.

    """
    return {"asset_id": asset_id, "price": price}


def _mock_feed(events: list[dict[str, Any]]) -> MagicMock:
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


class TestPaperTradingEngine:
    """Tests for PaperTradingEngine."""

    @pytest.mark.asyncio
    async def test_run_returns_result(self) -> None:
        """Verify run() returns a PaperTradingResult."""
        events = [_make_ws_event(price=p) for p in ["0.60", "0.60", "0.60"]]
        client = _mock_client()
        strategy = PMMeanReversionStrategy(period=3, z_threshold=Decimal("1.5"))
        config = _make_config()
        feed = _mock_feed(events)
        engine = PaperTradingEngine(client, strategy, config, feed=feed)

        result = await engine.run(max_ticks=3)

        assert isinstance(result, PaperTradingResult)
        assert result.strategy_name == strategy.name
        assert result.initial_capital == _INITIAL_CAPITAL

    @pytest.mark.asyncio
    async def test_snapshots_counted(self) -> None:
        """Verify snapshots_processed is incremented correctly."""
        events = [_make_ws_event(price=p) for p in ["0.60", "0.60", "0.60"]]
        client = _mock_client()
        strategy = PMMeanReversionStrategy(period=3, z_threshold=Decimal("1.5"))
        config = _make_config()
        feed = _mock_feed(events)
        engine = PaperTradingEngine(client, strategy, config, feed=feed)

        result = await engine.run(max_ticks=3)

        expected_snapshots = 3
        assert result.snapshots_processed == expected_snapshots

    @pytest.mark.asyncio
    async def test_bootstrap_failure_returns_empty_result(self) -> None:
        """Verify that bootstrap failure with no assets returns clean result."""
        client = AsyncMock()
        client.get_market = AsyncMock(side_effect=PolymarketAPIError(msg="API down", status_code=0))
        strategy = PMMeanReversionStrategy(period=3, z_threshold=Decimal("1.5"))
        config = _make_config()
        feed = _mock_feed([])
        engine = PaperTradingEngine(client, strategy, config, feed=feed)

        result = await engine.run(max_ticks=2)

        assert result.snapshots_processed == 0

    @pytest.mark.asyncio
    async def test_signal_triggers_trade(self) -> None:
        """Verify a strategy signal results in a paper trade."""
        # Feed stable prices then a sharp drop to trigger mean reversion BUY
        prices = ["0.60"] * 6 + ["0.40"]
        events = [_make_ws_event(price=p) for p in prices]
        client = _mock_client()
        strategy = PMMeanReversionStrategy(period=5, z_threshold=Decimal("1.5"))
        config = _make_config()
        feed = _mock_feed(events)
        engine = PaperTradingEngine(client, strategy, config, feed=feed)

        result = await engine.run(max_ticks=len(prices))

        buy_trades = [t for t in result.trades if t.side == Side.BUY]
        assert len(buy_trades) > 0

    @pytest.mark.asyncio
    async def test_sell_signal_closes_position(self) -> None:
        """Verify a SELL signal closes an existing position."""
        # Stable → drop (BUY) → spike (SELL)
        prices = ["0.60"] * 6 + ["0.40"] + ["0.60"] * 5 + ["0.80"]
        events = [_make_ws_event(price=p) for p in prices]
        client = _mock_client()
        strategy = PMMeanReversionStrategy(period=5, z_threshold=Decimal("1.5"))
        config = _make_config()
        feed = _mock_feed(events)
        engine = PaperTradingEngine(client, strategy, config, feed=feed)

        result = await engine.run(max_ticks=len(prices))

        sell_trades = [t for t in result.trades if t.side == Side.SELL]
        assert len(sell_trades) > 0

    @pytest.mark.asyncio
    async def test_empty_markets_returns_clean_result(self) -> None:
        """Verify engine with no markets returns clean result."""
        client = _mock_client()
        strategy = PMMeanReversionStrategy()
        config = BotConfig(markets=())
        feed = _mock_feed([])
        engine = PaperTradingEngine(client, strategy, config, feed=feed)

        result = await engine.run(max_ticks=3)

        assert result.snapshots_processed == 0
        assert result.trades == ()

    @pytest.mark.asyncio
    async def test_trade_opened_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        """Verify a successful trade open is logged."""
        prices = ["0.60"] * 6 + ["0.40"]
        events = [_make_ws_event(price=p) for p in prices]
        client = _mock_client()
        strategy = PMMeanReversionStrategy(period=5, z_threshold=Decimal("1.5"))
        config = _make_config()
        feed = _mock_feed(events)
        engine = PaperTradingEngine(client, strategy, config, feed=feed)

        with caplog.at_level(logging.INFO, logger="trading_tools.apps.polymarket_bot.engine"):
            result = await engine.run(max_ticks=len(prices))

        buy_trades = [t for t in result.trades if t.side == Side.BUY]
        assert len(buy_trades) > 0
        assert any("TRADE OPENED" in msg for msg in caplog.messages)

    @pytest.mark.asyncio
    async def test_position_outcomes_only_set_on_success(self) -> None:
        """Verify _position_outcomes is only set when trade succeeds."""
        prices = ["0.60"] * 6 + ["0.40"]
        events = [_make_ws_event(price=p) for p in prices]
        client = _mock_client()
        strategy = PMMeanReversionStrategy(period=5, z_threshold=Decimal("1.5"))
        config = BotConfig(
            initial_capital=Decimal("0.001"),
            max_position_pct=Decimal("0.1"),
            kelly_fraction=Decimal("0.25"),
            max_history=100,
            markets=(_CONDITION_ID,),
        )
        feed = _mock_feed(events)
        engine = PaperTradingEngine(client, strategy, config, feed=feed)

        await engine.run(max_ticks=len(prices))

        # No position should be tracked if trade was rejected (near-zero capital)
        assert _CONDITION_ID not in engine._position_outcomes

    @pytest.mark.asyncio
    async def test_unknown_asset_event_ignored(self) -> None:
        """Verify events for unregistered asset IDs are silently ignored."""
        events = [_make_ws_event(asset_id="unknown_asset", price="0.55")]
        client = _mock_client()
        strategy = PMMeanReversionStrategy(period=3, z_threshold=Decimal("1.5"))
        config = _make_config()
        feed = _mock_feed(events)
        engine = PaperTradingEngine(client, strategy, config, feed=feed)

        result = await engine.run(max_ticks=1)

        assert result.snapshots_processed == 0

    @pytest.mark.asyncio
    async def test_feed_close_called_on_exit(self) -> None:
        """Verify MarketFeed.close() is called when the engine stops."""
        events = [_make_ws_event(price="0.60")]
        client = _mock_client()
        strategy = PMMeanReversionStrategy()
        config = _make_config()
        feed = _mock_feed(events)
        engine = PaperTradingEngine(client, strategy, config, feed=feed)

        await engine.run(max_ticks=1)

        feed.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_order_book_refreshed_before_trade(self) -> None:
        """Verify the order book is refreshed immediately before executing a trade."""
        prices = ["0.60"] * 6 + ["0.40"]
        events = [_make_ws_event(price=p) for p in prices]
        client = _mock_client()
        strategy = PMMeanReversionStrategy(period=5, z_threshold=Decimal("1.5"))
        config = _make_config()
        feed = _mock_feed(events)
        engine = PaperTradingEngine(client, strategy, config, feed=feed)

        await engine.run(max_ticks=len(prices))

        # get_order_book is called at bootstrap + once per trade signal
        ob_call_count = client.get_order_book.call_count
        min_expected_calls = 2  # 1 bootstrap + 1 pre-trade refresh
        assert ob_call_count >= min_expected_calls, (
            f"Expected at least {min_expected_calls} get_order_book calls "
            f"(bootstrap + pre-trade refresh), got {ob_call_count}"
        )


_NEW_CONDITION_ID = "cond_rotated_market"


class TestMarketRotation:
    """Tests for 5-minute market rotation."""

    @pytest.mark.asyncio
    async def test_rotation_discovers_new_markets(self) -> None:
        """Verify window change triggers market re-discovery."""
        start_time = 1700000000

        client = _mock_client()
        client.discover_series_markets = AsyncMock(
            return_value=[(_NEW_CONDITION_ID, "2026-02-22T12:10:00Z")]
        )

        config = BotConfig(
            initial_capital=_INITIAL_CAPITAL,
            max_position_pct=Decimal("0.1"),
            kelly_fraction=Decimal("0.25"),
            max_history=100,
            markets=(_CONDITION_ID,),
            series_slugs=("btc-updown-5m",),
        )

        feed = _mock_feed([])

        with patch("trading_tools.apps.polymarket_bot.engine.time") as mock_time:
            mock_time.time.return_value = float(start_time)
            engine = PaperTradingEngine(
                client, strategy=PMMeanReversionStrategy(), config=config, feed=feed
            )
            engine._current_window = (start_time // 300) * 300

            await engine._rotate_markets()

        client.discover_series_markets.assert_called_once_with(["btc-updown-5m"])
        assert _NEW_CONDITION_ID in engine._active_markets

    @pytest.mark.asyncio
    async def test_rotation_closes_open_positions(self, caplog: pytest.LogCaptureFixture) -> None:
        """Verify rotation closes all open positions before discovering new markets."""
        start_time = 1700000000

        client = _mock_client()
        client.discover_series_markets = AsyncMock(
            return_value=[(_NEW_CONDITION_ID, "2026-02-22T12:10:00Z")]
        )

        config = BotConfig(
            initial_capital=_INITIAL_CAPITAL,
            max_position_pct=Decimal("0.1"),
            kelly_fraction=Decimal("0.25"),
            max_history=100,
            markets=(_CONDITION_ID,),
            series_slugs=("btc-updown-5m",),
        )

        feed = _mock_feed([])

        with patch("trading_tools.apps.polymarket_bot.engine.time") as mock_time:
            mock_time.time.return_value = float(start_time)
            engine = PaperTradingEngine(
                client,
                strategy=PMMeanReversionStrategy(),
                config=config,
                feed=feed,
            )
            engine._current_window = (start_time // 300) * 300

            # Simulate an open position
            engine._portfolio.open_position(
                condition_id=_CONDITION_ID,
                outcome="Yes",
                side=Side.BUY,
                price=Decimal("0.60"),
                quantity=Decimal(10),
                timestamp=start_time,
                reason="test",
                edge=Decimal("0.05"),
            )
            engine._position_outcomes[_CONDITION_ID] = "Yes"

            with caplog.at_level(logging.INFO, logger="trading_tools.apps.polymarket_bot.engine"):
                await engine._rotate_markets()

        assert _CONDITION_ID not in engine._portfolio.positions
        assert _CONDITION_ID not in engine._position_outcomes
        assert any("ROTATION CLOSE" in msg for msg in caplog.messages)

    @pytest.mark.asyncio
    async def test_rotation_discovery_failure_keeps_old_markets(self) -> None:
        """Verify discovery failure preserves existing active markets."""
        start_time = 1700000000

        client = _mock_client()
        client.discover_series_markets = AsyncMock(
            side_effect=PolymarketAPIError(msg="API down", status_code=0)
        )

        config = BotConfig(
            initial_capital=_INITIAL_CAPITAL,
            max_position_pct=Decimal("0.1"),
            kelly_fraction=Decimal("0.25"),
            max_history=100,
            markets=(_CONDITION_ID,),
            series_slugs=("btc-updown-5m",),
        )

        feed = _mock_feed([])

        with patch("trading_tools.apps.polymarket_bot.engine.time") as mock_time:
            mock_time.time.return_value = float(start_time)
            engine = PaperTradingEngine(
                client,
                strategy=PMMeanReversionStrategy(),
                config=config,
                feed=feed,
            )
            engine._current_window = (start_time // 300) * 300

            await engine._rotate_markets()

        assert _CONDITION_ID in engine._active_markets

    @pytest.mark.asyncio
    async def test_rotation_emits_perf_log(self, caplog: pytest.LogCaptureFixture) -> None:
        """Verify performance metrics are logged after market rotation."""
        start_time = 1700000000

        client = _mock_client()
        client.discover_series_markets = AsyncMock(
            return_value=[(_NEW_CONDITION_ID, "2026-02-22T12:10:00Z")]
        )

        config = BotConfig(
            initial_capital=_INITIAL_CAPITAL,
            max_position_pct=Decimal("0.1"),
            kelly_fraction=Decimal("0.25"),
            max_history=100,
            markets=(_CONDITION_ID,),
            series_slugs=("btc-updown-5m",),
        )

        feed = _mock_feed([])

        with patch("trading_tools.apps.polymarket_bot.engine.time") as mock_time:
            mock_time.time.return_value = float(start_time)
            engine = PaperTradingEngine(
                client,
                strategy=PMMeanReversionStrategy(),
                config=config,
                feed=feed,
            )
            engine._current_window = (start_time // 300) * 300

            with caplog.at_level(logging.INFO, logger="trading_tools.apps.polymarket_bot.engine"):
                await engine._rotate_markets()

        perf_messages = [msg for msg in caplog.messages if "[PERF" in msg]
        assert len(perf_messages) == 1

    @pytest.mark.asyncio
    async def test_rotation_updates_feed_subscription(self) -> None:
        """Verify rotation calls update_subscription on the feed."""
        start_time = 1700000000

        client = _mock_client()
        client.discover_series_markets = AsyncMock(
            return_value=[(_NEW_CONDITION_ID, "2026-02-22T12:10:00Z")]
        )

        config = BotConfig(
            initial_capital=_INITIAL_CAPITAL,
            max_position_pct=Decimal("0.1"),
            kelly_fraction=Decimal("0.25"),
            max_history=100,
            markets=(_CONDITION_ID,),
            series_slugs=("btc-updown-5m",),
        )

        feed = _mock_feed([])

        with patch("trading_tools.apps.polymarket_bot.engine.time") as mock_time:
            mock_time.time.return_value = float(start_time)
            engine = PaperTradingEngine(
                client,
                strategy=PMMeanReversionStrategy(),
                config=config,
                feed=feed,
            )
            engine._current_window = (start_time // 300) * 300

            await engine._rotate_markets()

        feed.update_subscription.assert_called_once()
