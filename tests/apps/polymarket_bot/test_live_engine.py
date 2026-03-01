"""Tests for LiveTradingEngine WebSocket-driven event loop."""

import asyncio
import logging
import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trading_tools.apps.polymarket_bot.live_engine import LiveTradingEngine
from trading_tools.apps.polymarket_bot.models import (
    BotConfig,
    LiveTradingResult,
)
from trading_tools.apps.polymarket_bot.strategies.mean_reversion import (
    PMMeanReversionStrategy,
)
from trading_tools.clients.polymarket.models import (
    Balance,
    Market,
    MarketToken,
    OrderBook,
    OrderLevel,
    OrderResponse,
    RedeemablePosition,
)
from trading_tools.core.models import ZERO, Side

_CONDITION_ID = "cond_live_engine_test"
_YES_TOKEN_ID = "yes_tok_live"
_NO_TOKEN_ID = "no_tok_live"
_ORDER_ID = "order_live_123"
_INITIAL_BALANCE = Decimal("1000.00")


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
        max_position_pct=Decimal("0.1"),
        kelly_fraction=Decimal("0.25"),
        max_history=100,
        markets=(_CONDITION_ID,),
    )


def _make_order_response(filled: Decimal = ZERO) -> OrderResponse:
    """Create a test OrderResponse.

    Args:
        filled: Filled quantity.

    Returns:
        OrderResponse with standard test data.

    """
    return OrderResponse(
        order_id=_ORDER_ID,
        status="matched",
        token_id=_YES_TOKEN_ID,
        side="BUY",
        price=Decimal("0.60"),
        size=Decimal(10),
        filled=filled,
    )


_PORTFOLIO_VALUE = Decimal("1050.00")


def _mock_client(
    market: Market | None = None,
    order_book: OrderBook | None = None,
) -> AsyncMock:
    """Create a mock PolymarketClient for live engine tests.

    Args:
        market: Market to return from get_market.
        order_book: OrderBook to return from get_order_book.

    Returns:
        AsyncMock configured as an authenticated PolymarketClient.

    """
    client = AsyncMock()
    client.get_market = AsyncMock(return_value=market or _make_market())
    client.get_order_book = AsyncMock(return_value=order_book or _make_order_book())
    client.get_balance = AsyncMock(
        return_value=Balance(
            asset_type="COLLATERAL",
            balance=_INITIAL_BALANCE,
            allowance=Decimal(10000),
        ),
    )
    client.get_portfolio_value = AsyncMock(return_value=_PORTFOLIO_VALUE)
    client.place_order = AsyncMock(return_value=_make_order_response(filled=Decimal(10)))
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


class TestLiveTradingEngine:
    """Tests for LiveTradingEngine."""

    @pytest.mark.asyncio
    async def test_run_returns_result(self) -> None:
        """Verify run() returns a LiveTradingResult."""
        events = [_make_ws_event(price=p) for p in ["0.60", "0.60", "0.60"]]
        client = _mock_client()
        strategy = PMMeanReversionStrategy(period=3, z_threshold=Decimal("1.5"))
        config = _make_config()
        feed = _mock_feed(events)
        engine = LiveTradingEngine(client, strategy, config, feed=feed)

        result = await engine.run(max_ticks=3)

        assert isinstance(result, LiveTradingResult)
        assert result.strategy_name == strategy.name
        assert result.initial_balance == _INITIAL_BALANCE

    @pytest.mark.asyncio
    async def test_snapshots_counted(self) -> None:
        """Verify snapshots_processed is incremented correctly."""
        events = [_make_ws_event(price=p) for p in ["0.60", "0.60", "0.60"]]
        client = _mock_client()
        strategy = PMMeanReversionStrategy(period=3, z_threshold=Decimal("1.5"))
        config = _make_config()
        feed = _mock_feed(events)
        engine = LiveTradingEngine(client, strategy, config, feed=feed)

        result = await engine.run(max_ticks=3)

        expected_snapshots = 3
        assert result.snapshots_processed == expected_snapshots

    @pytest.mark.asyncio
    async def test_bootstrap_failure_returns_empty_result(self) -> None:
        """Verify bootstrap failure with no assets returns clean result."""
        client = AsyncMock()
        client.get_market = AsyncMock(side_effect=Exception("API down"))
        client.get_balance = AsyncMock(
            return_value=Balance(
                asset_type="COLLATERAL",
                balance=_INITIAL_BALANCE,
                allowance=Decimal(10000),
            ),
        )
        client.place_order = AsyncMock(return_value=_make_order_response())
        strategy = PMMeanReversionStrategy(period=3, z_threshold=Decimal("1.5"))
        config = _make_config()
        feed = _mock_feed([])
        engine = LiveTradingEngine(client, strategy, config, feed=feed)

        result = await engine.run(max_ticks=2)

        assert result.snapshots_processed == 0

    @pytest.mark.asyncio
    async def test_signal_triggers_trade(self) -> None:
        """Verify a strategy signal results in a live trade."""
        prices = ["0.60"] * 6 + ["0.40"]
        events = [_make_ws_event(price=p) for p in prices]
        client = _mock_client()
        strategy = PMMeanReversionStrategy(period=5, z_threshold=Decimal("1.5"))
        config = _make_config()
        feed = _mock_feed(events)
        engine = LiveTradingEngine(client, strategy, config, feed=feed)

        result = await engine.run(max_ticks=len(prices))

        buy_trades = [t for t in result.trades if t.side == Side.BUY]
        assert len(buy_trades) > 0

    @pytest.mark.asyncio
    async def test_opened_position_stops_processing(self) -> None:
        """Verify that once a position is opened, events for that market are skipped."""
        prices = ["0.60"] * 6 + ["0.40", "0.60", "0.60"]
        events = [_make_ws_event(price=p) for p in prices]
        client = _mock_client()
        strategy = PMMeanReversionStrategy(period=5, z_threshold=Decimal("1.5"))
        config = _make_config()
        feed = _mock_feed(events)
        engine = LiveTradingEngine(client, strategy, config, feed=feed)

        result = await engine.run(max_ticks=len(prices))

        buy_trades = [t for t in result.trades if t.side == Side.BUY]
        assert len(buy_trades) > 0
        # After the BUY, subsequent events should not increment snapshots
        buy_tick = 7
        assert result.snapshots_processed < len(prices), (
            f"Expected fewer than {len(prices)} snapshots, got {result.snapshots_processed} "
            f"(market should stop being processed after BUY on tick {buy_tick})"
        )

    @pytest.mark.asyncio
    async def test_loss_limit_stops_engine(self) -> None:
        """Verify the engine stops when loss limit is breached."""
        events = [_make_ws_event(price="0.60")] * 10
        client = _mock_client()
        strategy = PMMeanReversionStrategy(period=3, z_threshold=Decimal("1.5"))
        config = _make_config()

        # Yield one event, then simulate balance drop before subsequent events
        event_count = 0
        low_balance = Decimal("850.00")

        async def feed_with_loss(asset_ids: list[str]) -> Any:  # noqa: ARG001
            nonlocal event_count
            for event in events:
                event_count += 1
                if event_count == 2:  # noqa: PLR2004
                    # Simulate balance drop after first event
                    engine._portfolio._balance = low_balance
                yield event

        feed = MagicMock()
        feed.stream = feed_with_loss
        feed.close = AsyncMock()
        feed.update_subscription = AsyncMock()

        engine = LiveTradingEngine(
            client,
            strategy,
            config,
            feed=feed,
            max_loss_pct=Decimal("0.10"),
        )

        result = await engine.run(max_ticks=10)

        # Should stop early due to loss limit after balance dropped
        max_expected_ticks = 3
        assert result.snapshots_processed <= max_expected_ticks

    @pytest.mark.asyncio
    async def test_shutdown_flag_stops_engine(self) -> None:
        """Verify setting _shutdown flag stops the engine."""
        events = [_make_ws_event(price="0.60")] * 100
        client = _mock_client()
        strategy = PMMeanReversionStrategy(period=3, z_threshold=Decimal("1.5"))
        config = _make_config()
        feed = _mock_feed(events)
        engine = LiveTradingEngine(client, strategy, config, feed=feed)

        # Set shutdown before running
        engine._shutdown = True

        result = await engine.run(max_ticks=100)

        assert result.snapshots_processed == 0

    @pytest.mark.asyncio
    async def test_trade_opened_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        """Verify a successful trade open is logged with order details."""
        prices = ["0.60"] * 6 + ["0.40"]
        events = [_make_ws_event(price=p) for p in prices]
        client = _mock_client()
        strategy = PMMeanReversionStrategy(period=5, z_threshold=Decimal("1.5"))
        config = _make_config()
        feed = _mock_feed(events)
        engine = LiveTradingEngine(client, strategy, config, feed=feed)

        with caplog.at_level(
            logging.INFO,
            logger="trading_tools.apps.polymarket_bot.live_engine",
        ):
            result = await engine.run(max_ticks=len(prices))

        buy_trades = [t for t in result.trades if t.side == Side.BUY]
        assert len(buy_trades) > 0
        assert any("TRADE OPENED" in msg for msg in caplog.messages)

    @pytest.mark.asyncio
    async def test_empty_markets_returns_clean_result(self) -> None:
        """Verify engine with no markets returns clean result."""
        client = _mock_client()
        strategy = PMMeanReversionStrategy()
        config = BotConfig(markets=())
        feed = _mock_feed([])
        engine = LiveTradingEngine(client, strategy, config, feed=feed)

        result = await engine.run(max_ticks=3)

        assert result.snapshots_processed == 0
        assert result.trades == ()

    @pytest.mark.asyncio
    async def test_token_ids_cached_from_bootstrap(self) -> None:
        """Verify token IDs are cached during bootstrap."""
        events = [_make_ws_event(price="0.60")]
        client = _mock_client()
        strategy = PMMeanReversionStrategy(period=3, z_threshold=Decimal("1.5"))
        config = _make_config()
        feed = _mock_feed(events)
        engine = LiveTradingEngine(client, strategy, config, feed=feed)

        await engine.run(max_ticks=1)

        assert _CONDITION_ID in engine._token_ids
        assert engine._token_ids[_CONDITION_ID] == (_YES_TOKEN_ID, _NO_TOKEN_ID)

    @pytest.mark.asyncio
    async def test_feed_close_called_on_exit(self) -> None:
        """Verify MarketFeed.close() is called when the engine stops."""
        events = [_make_ws_event(price="0.60")]
        client = _mock_client()
        strategy = PMMeanReversionStrategy()
        config = _make_config()
        feed = _mock_feed(events)
        engine = LiveTradingEngine(client, strategy, config, feed=feed)

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
        engine = LiveTradingEngine(client, strategy, config, feed=feed)

        # Bootstrap calls get_order_book once; reset to track trade-time calls
        await engine.run(max_ticks=len(prices))

        # get_order_book is called at bootstrap + once per trade signal
        ob_call_count = client.get_order_book.call_count
        min_expected_calls = 2  # 1 bootstrap + 1 pre-trade refresh
        assert ob_call_count >= min_expected_calls, (
            f"Expected at least {min_expected_calls} get_order_book calls "
            f"(bootstrap + pre-trade refresh), got {ob_call_count}"
        )


_NEW_CONDITION_ID = "cond_rotated_live_market"


class TestLiveMarketRotation:
    """Tests for 5-minute market rotation in live engine."""

    @pytest.mark.asyncio
    async def test_rotation_discovers_new_markets(self) -> None:
        """Verify window change triggers market re-discovery."""
        start_time = 1700000000

        client = _mock_client()
        client.discover_series_markets = AsyncMock(
            return_value=[(_NEW_CONDITION_ID, "2026-02-22T12:10:00Z")],
        )

        config = BotConfig(
            max_position_pct=Decimal("0.1"),
            kelly_fraction=Decimal("0.25"),
            max_history=100,
            markets=(_CONDITION_ID,),
            series_slugs=("btc-updown-5m",),
        )

        feed = _mock_feed([])

        with patch("trading_tools.apps.polymarket_bot.live_engine.time") as mock_time:
            mock_time.time.return_value = float(start_time)

            engine = LiveTradingEngine(
                client,
                strategy=PMMeanReversionStrategy(),
                config=config,
                feed=feed,
            )
            engine._current_window = (start_time // 300) * 300

            await engine._rotate_markets()

        client.discover_series_markets.assert_called_once_with(["btc-updown-5m"])
        assert _NEW_CONDITION_ID in engine._active_markets

    @pytest.mark.asyncio
    async def test_no_rotation_without_series_slugs(self) -> None:
        """Verify rotation loop exits immediately when series_slugs is empty."""
        events = [_make_ws_event(price="0.60")] * 2
        client = _mock_client()
        client.discover_series_markets = AsyncMock()

        config = _make_config()
        feed = _mock_feed(events)
        engine = LiveTradingEngine(
            client,
            strategy=PMMeanReversionStrategy(),
            config=config,
            feed=feed,
        )

        await engine.run(max_ticks=2)

        client.discover_series_markets.assert_not_called()

    @pytest.mark.asyncio
    async def test_rotation_emits_perf_log(self, caplog: pytest.LogCaptureFixture) -> None:
        """Verify performance metrics are logged after market rotation."""
        start_time = 1700000000

        client = _mock_client()
        client.discover_series_markets = AsyncMock(
            return_value=[(_NEW_CONDITION_ID, "2026-02-22T12:10:00Z")],
        )

        config = BotConfig(
            max_position_pct=Decimal("0.1"),
            kelly_fraction=Decimal("0.25"),
            max_history=100,
            markets=(_CONDITION_ID,),
            series_slugs=("btc-updown-5m",),
        )

        feed = _mock_feed([])

        with patch("trading_tools.apps.polymarket_bot.live_engine.time") as mock_time:
            mock_time.time.return_value = float(start_time)

            engine = LiveTradingEngine(
                client,
                strategy=PMMeanReversionStrategy(),
                config=config,
                feed=feed,
            )
            engine._current_window = (start_time // 300) * 300

            with caplog.at_level(
                logging.INFO,
                logger="trading_tools.apps.polymarket_bot.live_engine",
            ):
                await engine._rotate_markets()

        perf_messages = [msg for msg in caplog.messages if "[PERF" in msg]
        assert len(perf_messages) == 1
        assert "portfolio=$" in perf_messages[0]

    @pytest.mark.asyncio
    async def test_rotation_updates_feed_subscription(self) -> None:
        """Verify rotation calls update_subscription on the feed."""
        start_time = 1700000000

        client = _mock_client()
        client.discover_series_markets = AsyncMock(
            return_value=[(_NEW_CONDITION_ID, "2026-02-22T12:10:00Z")],
        )

        config = BotConfig(
            max_position_pct=Decimal("0.1"),
            kelly_fraction=Decimal("0.25"),
            max_history=100,
            markets=(_CONDITION_ID,),
            series_slugs=("btc-updown-5m",),
        )

        feed = _mock_feed([])

        with patch("trading_tools.apps.polymarket_bot.live_engine.time") as mock_time:
            mock_time.time.return_value = float(start_time)

            engine = LiveTradingEngine(
                client,
                strategy=PMMeanReversionStrategy(),
                config=config,
                feed=feed,
            )
            engine._current_window = (start_time // 300) * 300

            await engine._rotate_markets()

        feed.update_subscription.assert_called_once()


class TestAutoRedeem:
    """Tests for auto-redeem of redeemable positions on rotation."""

    @pytest.mark.asyncio
    async def test_redeem_calls_ctf_redemption(self, caplog: pytest.LogCaptureFixture) -> None:
        """Verify auto-redeem calls on-chain CTF redemption with condition IDs."""
        start_time = 1700000000

        client = _mock_client()
        client.discover_series_markets = AsyncMock(
            return_value=[(_NEW_CONDITION_ID, "2026-02-22T12:10:00Z")],
        )
        client.get_redeemable_positions = AsyncMock(
            return_value=[
                RedeemablePosition(
                    condition_id="0xresolved1",
                    token_id="tok_resolved_1",
                    outcome="Down",
                    size=Decimal("10.0"),
                    title="ETH Up or Down - Feb 24",
                ),
            ],
        )
        client.redeem_positions = AsyncMock(return_value=1)

        config = BotConfig(
            max_position_pct=Decimal("0.1"),
            kelly_fraction=Decimal("0.25"),
            max_history=100,
            markets=(_CONDITION_ID,),
            series_slugs=("btc-updown-5m",),
        )

        feed = _mock_feed([])

        with patch("trading_tools.apps.polymarket_bot.live_engine.time") as mock_time:
            mock_time.time.return_value = float(start_time)

            engine = LiveTradingEngine(
                client,
                strategy=PMMeanReversionStrategy(),
                config=config,
                feed=feed,
                auto_redeem=True,
            )
            engine._current_window = (start_time // 300) * 300

            with caplog.at_level(
                logging.INFO,
                logger="trading_tools.apps.polymarket_bot.live_engine",
            ):
                await engine._rotate_markets()
                # Let the background redeem task complete
                assert engine._redeem_task is not None
                await engine._redeem_task

        client.get_redeemable_positions.assert_called_once()
        client.redeem_positions.assert_called_once_with(["0xresolved1"])
        assert any("AUTO-REDEEM: redeemed" in msg for msg in caplog.messages)

    @pytest.mark.asyncio
    async def test_redeem_skips_small_positions(self, caplog: pytest.LogCaptureFixture) -> None:
        """Verify auto-redeem skips positions below minimum order size."""
        start_time = 1700000000

        client = _mock_client()
        client.discover_series_markets = AsyncMock(
            return_value=[(_NEW_CONDITION_ID, "2026-02-22T12:10:00Z")],
        )
        client.get_redeemable_positions = AsyncMock(
            return_value=[
                RedeemablePosition(
                    condition_id="0xsmall",
                    token_id="tok_small",
                    outcome="Up",
                    size=Decimal("2.0"),
                    title="Small position",
                ),
            ],
        )
        client.redeem_positions = AsyncMock(return_value=0)

        config = BotConfig(
            max_position_pct=Decimal("0.1"),
            kelly_fraction=Decimal("0.25"),
            max_history=100,
            markets=(_CONDITION_ID,),
            series_slugs=("btc-updown-5m",),
        )

        feed = _mock_feed([])

        with patch("trading_tools.apps.polymarket_bot.live_engine.time") as mock_time:
            mock_time.time.return_value = float(start_time)

            engine = LiveTradingEngine(
                client,
                strategy=PMMeanReversionStrategy(),
                config=config,
                feed=feed,
                auto_redeem=True,
            )
            engine._current_window = (start_time // 300) * 300

            with caplog.at_level(
                logging.INFO,
                logger="trading_tools.apps.polymarket_bot.live_engine",
            ):
                await engine._rotate_markets()
                # No background task should be created for undersized positions
                assert engine._redeem_task is None

        assert any("REDEEM skip" in msg for msg in caplog.messages)
        client.redeem_positions.assert_not_called()

    @pytest.mark.asyncio
    async def test_redeem_disabled_skips_discovery(self) -> None:
        """Verify auto-redeem is skipped when disabled."""
        start_time = 1700000000

        client = _mock_client()
        client.discover_series_markets = AsyncMock(
            return_value=[(_NEW_CONDITION_ID, "2026-02-22T12:10:00Z")],
        )
        client.get_redeemable_positions = AsyncMock()

        config = BotConfig(
            max_position_pct=Decimal("0.1"),
            kelly_fraction=Decimal("0.25"),
            max_history=100,
            markets=(_CONDITION_ID,),
            series_slugs=("btc-updown-5m",),
        )

        feed = _mock_feed([])

        with patch("trading_tools.apps.polymarket_bot.live_engine.time") as mock_time:
            mock_time.time.return_value = float(start_time)

            engine = LiveTradingEngine(
                client,
                strategy=PMMeanReversionStrategy(),
                config=config,
                feed=feed,
                auto_redeem=False,
            )
            engine._current_window = (start_time // 300) * 300

            await engine._rotate_markets()

        client.get_redeemable_positions.assert_not_called()

    @pytest.mark.asyncio
    async def test_redeem_ctf_failure_logged_not_raised(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Verify CTF redemption failure is logged without crashing the engine."""
        start_time = 1700000000

        client = _mock_client()
        client.discover_series_markets = AsyncMock(
            return_value=[(_NEW_CONDITION_ID, "2026-02-22T12:10:00Z")],
        )
        client.get_redeemable_positions = AsyncMock(
            return_value=[
                RedeemablePosition(
                    condition_id="0xfailing",
                    token_id="tok_fail",
                    outcome="Up",
                    size=Decimal("10.0"),
                    title="Failing redemption",
                ),
            ],
        )
        client.redeem_positions = AsyncMock(side_effect=Exception("RPC timeout"))

        config = BotConfig(
            max_position_pct=Decimal("0.1"),
            kelly_fraction=Decimal("0.25"),
            max_history=100,
            markets=(_CONDITION_ID,),
            series_slugs=("btc-updown-5m",),
        )

        feed = _mock_feed([])

        with patch("trading_tools.apps.polymarket_bot.live_engine.time") as mock_time:
            mock_time.time.return_value = float(start_time)

            engine = LiveTradingEngine(
                client,
                strategy=PMMeanReversionStrategy(),
                config=config,
                feed=feed,
                auto_redeem=True,
            )
            engine._current_window = (start_time // 300) * 300

            with caplog.at_level(
                logging.WARNING,
                logger="trading_tools.apps.polymarket_bot.live_engine",
            ):
                await engine._rotate_markets()
                # Let the background redeem task complete
                assert engine._redeem_task is not None
                await engine._redeem_task

        client.redeem_positions.assert_called_once_with(["0xfailing"])
        assert any("CTF redemption failed" in msg for msg in caplog.messages)

    @pytest.mark.asyncio
    async def test_redeem_cancels_previous_running_task(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Verify auto-redeem cancels previous task and starts a new one."""
        client = _mock_client()
        client.get_redeemable_positions = AsyncMock(
            return_value=[
                RedeemablePosition(
                    condition_id="0xpending",
                    token_id="tok_pending",
                    outcome="Up",
                    size=Decimal("10.0"),
                    title="Pending redemption",
                ),
            ],
        )
        client.redeem_positions = AsyncMock(return_value=1)

        config = _make_config()
        feed = _mock_feed([])
        engine = LiveTradingEngine(
            client,
            strategy=PMMeanReversionStrategy(),
            config=config,
            feed=feed,
            auto_redeem=True,
        )
        # Simulate a still-running redeem task
        old_task = asyncio.create_task(asyncio.sleep(10))
        engine._redeem_task = old_task

        with caplog.at_level(
            logging.INFO,
            logger="trading_tools.apps.polymarket_bot.live_engine",
        ):
            await engine._redeem_resolved()

        assert old_task.cancelling()
        assert any("cancelling previous" in msg for msg in caplog.messages)
        # New task should have been created
        assert engine._redeem_task is not None
        assert engine._redeem_task is not old_task


class TestComputeSleep:
    """Tests for adaptive sleep timing via _compute_sleep()."""

    def test_returns_ob_refresh_without_end_times(self) -> None:
        """Verify fallback to order_book_refresh_seconds when no end times are set."""
        ob_refresh = 30
        client = _mock_client()
        strategy = PMMeanReversionStrategy()
        config = BotConfig(order_book_refresh_seconds=ob_refresh, markets=(_CONDITION_ID,))
        feed = _mock_feed([])
        engine = LiveTradingEngine(client, strategy, config, feed=feed)

        result = engine._compute_sleep()

        assert result == float(ob_refresh)

    def test_returns_large_sleep_far_from_window(self) -> None:
        """Verify long sleep when market end is far away."""
        client = _mock_client()
        strategy = PMMeanReversionStrategy()
        snipe_window = 60
        config = BotConfig(
            order_book_refresh_seconds=30,
            snipe_window_seconds=snipe_window,
            markets=(_CONDITION_ID,),
            market_end_times=((_CONDITION_ID, "2026-12-31T00:00:00Z"),),
        )
        feed = _mock_feed([])
        engine = LiveTradingEngine(client, strategy, config, feed=feed)

        result = engine._compute_sleep()

        # Should be much larger than snipe_poll_seconds
        assert result > snipe_window

    def test_returns_snipe_poll_inside_window(self) -> None:
        """Verify fast polling when inside the snipe window."""
        client = _mock_client()
        strategy = PMMeanReversionStrategy()
        snipe_poll = 1
        snipe_window = 60
        # Set end time to 30 seconds from now (inside snipe window)
        now = time.time()
        end_time = now + 30
        end_iso = datetime.fromtimestamp(end_time, tz=UTC).isoformat()

        config = BotConfig(
            order_book_refresh_seconds=30,
            snipe_poll_seconds=snipe_poll,
            snipe_window_seconds=snipe_window,
            markets=(_CONDITION_ID,),
            market_end_times=((_CONDITION_ID, end_iso),),
        )
        feed = _mock_feed([])
        engine = LiveTradingEngine(client, strategy, config, feed=feed)

        result = engine._compute_sleep()

        assert result == float(snipe_poll)

    def test_returns_snipe_poll_at_buffer_boundary(self) -> None:
        """Verify fast polling when exactly at the buffer boundary."""
        client = _mock_client()
        strategy = PMMeanReversionStrategy()
        snipe_window = 60
        buffer = 5  # _SLEEP_BUFFER_SECONDS
        now = time.time()
        # Set end time exactly at snipe_window + buffer from now
        end_time = now + snipe_window + buffer
        end_iso = datetime.fromtimestamp(end_time, tz=UTC).isoformat()

        config = BotConfig(
            order_book_refresh_seconds=30,
            snipe_window_seconds=snipe_window,
            markets=(_CONDITION_ID,),
            market_end_times=((_CONDITION_ID, end_iso),),
        )
        feed = _mock_feed([])
        engine = LiveTradingEngine(client, strategy, config, feed=feed)

        result = engine._compute_sleep()

        assert result == float(config.snipe_poll_seconds)

    def test_uses_earliest_end_time(self) -> None:
        """Verify _compute_sleep uses the earliest market end time."""
        second_cid = "cond_second_market"
        client = _mock_client()
        strategy = PMMeanReversionStrategy()
        now = time.time()
        # One market ends in 20s (inside window), one in 200s (outside)
        early_iso = datetime.fromtimestamp(now + 20, tz=UTC).isoformat()
        late_iso = datetime.fromtimestamp(now + 200, tz=UTC).isoformat()

        config = BotConfig(
            order_book_refresh_seconds=30,
            snipe_window_seconds=60,
            markets=(_CONDITION_ID, second_cid),
            market_end_times=((_CONDITION_ID, late_iso), (second_cid, early_iso)),
        )
        feed = _mock_feed([])
        engine = LiveTradingEngine(client, strategy, config, feed=feed)

        result = engine._compute_sleep()

        # Earliest end is 20s away, inside snipe window → fast poll
        assert result == float(config.snipe_poll_seconds)

    def test_invalid_end_time_falls_back_to_ob_refresh(self) -> None:
        """Verify invalid ISO end times fall back to order_book_refresh_seconds."""
        ob_refresh = 30
        client = _mock_client()
        strategy = PMMeanReversionStrategy()
        config = BotConfig(
            order_book_refresh_seconds=ob_refresh,
            markets=(_CONDITION_ID,),
            market_end_times=((_CONDITION_ID, "not-a-date"),),
        )
        feed = _mock_feed([])
        engine = LiveTradingEngine(client, strategy, config, feed=feed)

        result = engine._compute_sleep()

        assert result == float(ob_refresh)
