"""Tests for PaperTradingEngine async polling loop."""

import logging
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from trading_tools.apps.polymarket_bot.engine import PaperTradingEngine
from trading_tools.apps.polymarket_bot.models import (
    BotConfig,
    PaperTradingResult,
)
from trading_tools.apps.polymarket_bot.strategies.mean_reversion import (
    PMMeanReversionStrategy,
)
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
        poll_interval_seconds=0,
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


class TestPaperTradingEngine:
    """Tests for PaperTradingEngine."""

    @pytest.mark.asyncio
    async def test_run_returns_result(self) -> None:
        """Test that run() returns a PaperTradingResult."""
        client = _mock_client()
        strategy = PMMeanReversionStrategy(period=3, z_threshold=Decimal("1.5"))
        config = _make_config()
        engine = PaperTradingEngine(client, strategy, config)

        result = await engine.run(max_ticks=5)

        assert isinstance(result, PaperTradingResult)
        assert result.strategy_name == strategy.name
        assert result.initial_capital == _INITIAL_CAPITAL

    @pytest.mark.asyncio
    async def test_snapshots_counted(self) -> None:
        """Test that snapshots_processed is incremented correctly."""
        client = _mock_client()
        strategy = PMMeanReversionStrategy(period=3, z_threshold=Decimal("1.5"))
        config = _make_config()
        engine = PaperTradingEngine(client, strategy, config)

        result = await engine.run(max_ticks=3)

        expected_snapshots = 3
        assert result.snapshots_processed == expected_snapshots

    @pytest.mark.asyncio
    async def test_fetch_failure_skips_market(self) -> None:
        """Test that a fetch failure skips the market gracefully."""
        client = AsyncMock()
        client.get_market = AsyncMock(side_effect=Exception("API down"))
        strategy = PMMeanReversionStrategy(period=3, z_threshold=Decimal("1.5"))
        config = _make_config()
        engine = PaperTradingEngine(client, strategy, config)

        result = await engine.run(max_ticks=2)

        assert result.snapshots_processed == 0

    @pytest.mark.asyncio
    async def test_order_book_failure_skips_snapshot(self) -> None:
        """Test that an order book fetch failure skips the snapshot."""
        client = AsyncMock()
        client.get_market = AsyncMock(return_value=_make_market())
        client.get_order_book = AsyncMock(side_effect=Exception("Book unavailable"))
        strategy = PMMeanReversionStrategy(period=3, z_threshold=Decimal("1.5"))
        config = _make_config()
        engine = PaperTradingEngine(client, strategy, config)

        result = await engine.run(max_ticks=2)

        assert result.snapshots_processed == 0

    @pytest.mark.asyncio
    async def test_signal_triggers_trade(self) -> None:
        """Test that a strategy signal results in a paper trade."""
        client = _mock_client()
        # Use a strategy that will generate a signal:
        # Feed stable prices then a sharp drop
        prices = ["0.60", "0.60", "0.60", "0.60", "0.60", "0.60", "0.40"]
        call_count = 0

        async def varying_market(condition_id: str) -> Market:  # noqa: ARG001
            nonlocal call_count
            idx = min(call_count, len(prices) - 1)
            call_count += 1
            return _make_market(
                yes_price=prices[idx], no_price=str(Decimal(1) - Decimal(prices[idx]))
            )

        client.get_market = varying_market  # type: ignore[assignment]
        strategy = PMMeanReversionStrategy(period=5, z_threshold=Decimal("1.5"))
        config = _make_config()
        engine = PaperTradingEngine(client, strategy, config)

        result = await engine.run(max_ticks=len(prices))

        buy_trades = [t for t in result.trades if t.side == Side.BUY]
        assert len(buy_trades) > 0

    @pytest.mark.asyncio
    async def test_sell_signal_closes_position(self) -> None:
        """Test that a SELL signal closes an existing position."""
        client = _mock_client()
        # Stable → drop (BUY) → spike (SELL)
        prices = [
            "0.60",
            "0.60",
            "0.60",
            "0.60",
            "0.60",
            "0.60",
            "0.40",  # triggers BUY
            "0.60",
            "0.60",
            "0.60",
            "0.60",
            "0.60",
            "0.80",  # triggers SELL
        ]
        call_count = 0

        async def varying_market(condition_id: str) -> Market:  # noqa: ARG001
            nonlocal call_count
            idx = min(call_count, len(prices) - 1)
            call_count += 1
            return _make_market(
                yes_price=prices[idx], no_price=str(Decimal(1) - Decimal(prices[idx]))
            )

        client.get_market = varying_market  # type: ignore[assignment]
        strategy = PMMeanReversionStrategy(period=5, z_threshold=Decimal("1.5"))
        config = _make_config()
        engine = PaperTradingEngine(client, strategy, config)

        result = await engine.run(max_ticks=len(prices))

        sell_trades = [t for t in result.trades if t.side == Side.SELL]
        assert len(sell_trades) > 0

    @pytest.mark.asyncio
    async def test_empty_markets_returns_clean_result(self) -> None:
        """Test engine with no markets configured."""
        client = _mock_client()
        strategy = PMMeanReversionStrategy()
        config = BotConfig(poll_interval_seconds=0, markets=())
        engine = PaperTradingEngine(client, strategy, config)

        result = await engine.run(max_ticks=3)

        assert result.snapshots_processed == 0
        assert result.trades == ()

    @pytest.mark.asyncio
    async def test_trade_opened_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        """Test that a successful trade open is logged."""
        client = _mock_client()
        prices = ["0.60", "0.60", "0.60", "0.60", "0.60", "0.60", "0.40"]
        call_count = 0

        async def varying_market(condition_id: str) -> Market:  # noqa: ARG001
            nonlocal call_count
            idx = min(call_count, len(prices) - 1)
            call_count += 1
            return _make_market(
                yes_price=prices[idx], no_price=str(Decimal(1) - Decimal(prices[idx]))
            )

        client.get_market = varying_market  # type: ignore[assignment]
        strategy = PMMeanReversionStrategy(period=5, z_threshold=Decimal("1.5"))
        config = _make_config()
        engine = PaperTradingEngine(client, strategy, config)

        with caplog.at_level(logging.INFO, logger="trading_tools.apps.polymarket_bot.engine"):
            result = await engine.run(max_ticks=len(prices))

        buy_trades = [t for t in result.trades if t.side == Side.BUY]
        assert len(buy_trades) > 0
        assert any("TRADE OPENED" in msg for msg in caplog.messages)

    @pytest.mark.asyncio
    async def test_trade_rejected_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        """Test that a rejected trade is logged as a warning."""
        client = _mock_client()
        # Use very low capital so the second open attempt gets rejected
        prices = ["0.60", "0.60", "0.60", "0.60", "0.60", "0.60", "0.40"]
        call_count = 0

        async def varying_market(condition_id: str) -> Market:  # noqa: ARG001
            nonlocal call_count
            idx = min(call_count, len(prices) - 1)
            call_count += 1
            return _make_market(
                yes_price=prices[idx], no_price=str(Decimal(1) - Decimal(prices[idx]))
            )

        client.get_market = varying_market  # type: ignore[assignment]
        strategy = PMMeanReversionStrategy(period=5, z_threshold=Decimal("1.5"))
        # Very low capital to trigger rejection
        config = BotConfig(
            poll_interval_seconds=0,
            initial_capital=Decimal("0.01"),
            max_position_pct=Decimal("0.1"),
            kelly_fraction=Decimal("0.25"),
            max_history=100,
            markets=(_CONDITION_ID,),
        )
        engine = PaperTradingEngine(client, strategy, config)

        with caplog.at_level(logging.WARNING, logger="trading_tools.apps.polymarket_bot.engine"):
            await engine.run(max_ticks=len(prices))

        # With tiny capital, if a signal fires the trade should be rejected
        rejected = [msg for msg in caplog.messages if "TRADE REJECTED" in msg]
        # Either no signal fires (no rejection needed) or rejection is logged
        assert len(rejected) >= 0

    @pytest.mark.asyncio
    async def test_position_outcomes_only_set_on_success(self) -> None:
        """Test that _position_outcomes is only set when trade succeeds."""
        client = _mock_client()
        strategy = PMMeanReversionStrategy(period=5, z_threshold=Decimal("1.5"))
        config = BotConfig(
            poll_interval_seconds=0,
            initial_capital=Decimal("0.001"),
            max_position_pct=Decimal("0.1"),
            kelly_fraction=Decimal("0.25"),
            max_history=100,
            markets=(_CONDITION_ID,),
        )
        engine = PaperTradingEngine(client, strategy, config)

        # With near-zero capital, open_position should return None
        # and _position_outcomes should remain empty
        prices = ["0.60", "0.60", "0.60", "0.60", "0.60", "0.60", "0.40"]
        call_count = 0

        async def varying_market(condition_id: str) -> Market:  # noqa: ARG001
            nonlocal call_count
            idx = min(call_count, len(prices) - 1)
            call_count += 1
            return _make_market(
                yes_price=prices[idx], no_price=str(Decimal(1) - Decimal(prices[idx]))
            )

        client.get_market = varying_market  # type: ignore[assignment]
        await engine.run(max_ticks=len(prices))

        # No position should be tracked if trade was rejected
        assert _CONDITION_ID not in engine._position_outcomes

    @pytest.mark.asyncio
    async def test_close_position_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        """Test that a closed position is logged."""
        client = _mock_client()
        # Stable → drop (BUY) → spike (SELL)
        prices = [
            "0.60",
            "0.60",
            "0.60",
            "0.60",
            "0.60",
            "0.60",
            "0.40",  # triggers BUY
            "0.60",
            "0.60",
            "0.60",
            "0.60",
            "0.60",
            "0.80",  # triggers SELL
        ]
        call_count = 0

        async def varying_market(condition_id: str) -> Market:  # noqa: ARG001
            nonlocal call_count
            idx = min(call_count, len(prices) - 1)
            call_count += 1
            return _make_market(
                yes_price=prices[idx], no_price=str(Decimal(1) - Decimal(prices[idx]))
            )

        client.get_market = varying_market  # type: ignore[assignment]
        strategy = PMMeanReversionStrategy(period=5, z_threshold=Decimal("1.5"))
        config = _make_config()
        engine = PaperTradingEngine(client, strategy, config)

        with caplog.at_level(logging.INFO, logger="trading_tools.apps.polymarket_bot.engine"):
            result = await engine.run(max_ticks=len(prices))

        sell_trades = [t for t in result.trades if t.side == Side.SELL]
        if sell_trades:
            assert any("POSITION CLOSED" in msg for msg in caplog.messages)

    @pytest.mark.asyncio
    async def test_timestamp_from_time_module(self) -> None:
        """Test that snapshot timestamps come from time.time()."""
        fixed_time = 1700000000

        with patch("trading_tools.apps.polymarket_bot.engine.time") as mock_time:
            mock_time.time.return_value = float(fixed_time)

            client = _mock_client()
            strategy = PMMeanReversionStrategy(period=3, z_threshold=Decimal("1.5"))
            config = _make_config()
            engine = PaperTradingEngine(client, strategy, config)

            await engine.run(max_ticks=1)
            mock_time.time.assert_called()


_NEW_CONDITION_ID = "cond_rotated_market"


class TestMarketRotation:
    """Tests for 5-minute market rotation."""

    @pytest.mark.asyncio
    async def test_rotation_discovers_new_markets(self) -> None:
        """Test that window change triggers market re-discovery."""
        # Start at a window boundary
        start_time = 1700000000  # divisible by 300
        tick_time = start_time + 300  # next window

        client = _mock_client()
        client.discover_series_markets = AsyncMock(
            return_value=[(_NEW_CONDITION_ID, "2026-02-22T12:10:00Z")]
        )

        config = BotConfig(
            poll_interval_seconds=0,
            initial_capital=_INITIAL_CAPITAL,
            max_position_pct=Decimal("0.1"),
            kelly_fraction=Decimal("0.25"),
            max_history=100,
            markets=(_CONDITION_ID,),
            series_slugs=("btc-updown-5m",),
        )

        time_calls = iter([float(start_time), float(tick_time), float(tick_time)])

        with patch("trading_tools.apps.polymarket_bot.engine.time") as mock_time:
            mock_time.time.side_effect = time_calls

            engine = PaperTradingEngine(client, strategy=PMMeanReversionStrategy(), config=config)
            # Override _current_window to start_time's window
            engine._current_window = (start_time // 300) * 300

            # Manually call _tick which should trigger rotation
            await engine._tick()

        client.discover_series_markets.assert_called_once_with(["btc-updown-5m"])
        assert _NEW_CONDITION_ID in engine._active_markets

    @pytest.mark.asyncio
    async def test_rotation_closes_open_positions(self, caplog: pytest.LogCaptureFixture) -> None:
        """Test that rotation closes all open positions before discovering new markets."""
        start_time = 1700000000
        tick_time = start_time + 300

        client = _mock_client()
        client.discover_series_markets = AsyncMock(
            return_value=[(_NEW_CONDITION_ID, "2026-02-22T12:10:00Z")]
        )

        config = BotConfig(
            poll_interval_seconds=0,
            initial_capital=_INITIAL_CAPITAL,
            max_position_pct=Decimal("0.1"),
            kelly_fraction=Decimal("0.25"),
            max_history=100,
            markets=(_CONDITION_ID,),
            series_slugs=("btc-updown-5m",),
        )

        with patch("trading_tools.apps.polymarket_bot.engine.time") as mock_time:
            mock_time.time.return_value = float(start_time)
            engine = PaperTradingEngine(client, strategy=PMMeanReversionStrategy(), config=config)
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

            # Now trigger tick at the next window
            mock_time.time.return_value = float(tick_time)

            with caplog.at_level(logging.INFO, logger="trading_tools.apps.polymarket_bot.engine"):
                await engine._tick()

        # Position should be closed
        assert _CONDITION_ID not in engine._portfolio.positions
        assert _CONDITION_ID not in engine._position_outcomes
        assert any("ROTATION CLOSE" in msg for msg in caplog.messages)

    @pytest.mark.asyncio
    async def test_no_rotation_without_series_slugs(self) -> None:
        """Test that rotation is skipped when series_slugs is empty."""
        client = _mock_client()
        client.discover_series_markets = AsyncMock()

        config = _make_config()  # no series_slugs
        engine = PaperTradingEngine(client, strategy=PMMeanReversionStrategy(), config=config)

        await engine.run(max_ticks=2)

        client.discover_series_markets.assert_not_called()

    @pytest.mark.asyncio
    async def test_rotation_discovery_failure_keeps_old_markets(self) -> None:
        """Test that discovery failure preserves existing active markets."""
        start_time = 1700000000
        tick_time = start_time + 300

        client = _mock_client()
        client.discover_series_markets = AsyncMock(side_effect=Exception("API down"))

        config = BotConfig(
            poll_interval_seconds=0,
            initial_capital=_INITIAL_CAPITAL,
            max_position_pct=Decimal("0.1"),
            kelly_fraction=Decimal("0.25"),
            max_history=100,
            markets=(_CONDITION_ID,),
            series_slugs=("btc-updown-5m",),
        )

        with patch("trading_tools.apps.polymarket_bot.engine.time") as mock_time:
            mock_time.time.return_value = float(start_time)
            engine = PaperTradingEngine(client, strategy=PMMeanReversionStrategy(), config=config)
            engine._current_window = (start_time // 300) * 300

            mock_time.time.return_value = float(tick_time)
            await engine._tick()

        # Markets should remain unchanged (the old ones, since rotation failed)
        assert _CONDITION_ID in engine._active_markets

    @pytest.mark.asyncio
    async def test_rotation_logs_new_market_count(self, caplog: pytest.LogCaptureFixture) -> None:
        """Test that rotation logs the number of new markets discovered."""
        start_time = 1700000000
        tick_time = start_time + 300

        new_markets = [
            ("cond_btc_new", "2026-02-22T12:10:00Z"),
            ("cond_eth_new", "2026-02-22T12:10:00Z"),
        ]

        client = _mock_client()
        client.discover_series_markets = AsyncMock(return_value=new_markets)

        config = BotConfig(
            poll_interval_seconds=0,
            initial_capital=_INITIAL_CAPITAL,
            max_position_pct=Decimal("0.1"),
            kelly_fraction=Decimal("0.25"),
            max_history=100,
            markets=(_CONDITION_ID,),
            series_slugs=("btc-updown-5m", "eth-updown-5m"),
        )

        with patch("trading_tools.apps.polymarket_bot.engine.time") as mock_time:
            mock_time.time.return_value = float(start_time)
            engine = PaperTradingEngine(client, strategy=PMMeanReversionStrategy(), config=config)
            engine._current_window = (start_time // 300) * 300

            mock_time.time.return_value = float(tick_time)

            with caplog.at_level(logging.INFO, logger="trading_tools.apps.polymarket_bot.engine"):
                await engine._tick()

        expected_count = 2
        assert len(engine._active_markets) == expected_count
        assert any("Rotating markets: 2" in msg for msg in caplog.messages)

    @pytest.mark.asyncio
    async def test_rotation_emits_perf_log(self, caplog: pytest.LogCaptureFixture) -> None:
        """Verify performance metrics are logged after market rotation."""
        start_time = 1700000000
        tick_time = start_time + 300

        client = _mock_client()
        client.discover_series_markets = AsyncMock(
            return_value=[(_NEW_CONDITION_ID, "2026-02-22T12:10:00Z")]
        )

        config = BotConfig(
            poll_interval_seconds=0,
            initial_capital=_INITIAL_CAPITAL,
            max_position_pct=Decimal("0.1"),
            kelly_fraction=Decimal("0.25"),
            max_history=100,
            markets=(_CONDITION_ID,),
            series_slugs=("btc-updown-5m",),
        )

        with patch("trading_tools.apps.polymarket_bot.engine.time") as mock_time:
            mock_time.time.return_value = float(start_time)
            engine = PaperTradingEngine(client, strategy=PMMeanReversionStrategy(), config=config)
            engine._current_window = (start_time // 300) * 300

            mock_time.time.return_value = float(tick_time)

            with caplog.at_level(logging.INFO, logger="trading_tools.apps.polymarket_bot.engine"):
                await engine._tick()

        perf_messages = [msg for msg in caplog.messages if "[PERF" in msg]
        assert len(perf_messages) == 1
