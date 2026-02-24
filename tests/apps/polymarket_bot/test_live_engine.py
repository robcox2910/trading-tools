"""Tests for LiveTradingEngine async polling loop."""

import logging
from decimal import Decimal
from unittest.mock import AsyncMock, patch

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
        poll_interval_seconds=0,
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
    client.place_order = AsyncMock(return_value=_make_order_response(filled=Decimal(10)))
    return client


class TestLiveTradingEngine:
    """Tests for LiveTradingEngine."""

    @pytest.mark.asyncio
    async def test_run_returns_result(self) -> None:
        """Verify run() returns a LiveTradingResult."""
        client = _mock_client()
        strategy = PMMeanReversionStrategy(period=3, z_threshold=Decimal("1.5"))
        config = _make_config()
        engine = LiveTradingEngine(client, strategy, config)

        result = await engine.run(max_ticks=3)

        assert isinstance(result, LiveTradingResult)
        assert result.strategy_name == strategy.name
        assert result.initial_balance == _INITIAL_BALANCE

    @pytest.mark.asyncio
    async def test_snapshots_counted(self) -> None:
        """Verify snapshots_processed is incremented correctly."""
        client = _mock_client()
        strategy = PMMeanReversionStrategy(period=3, z_threshold=Decimal("1.5"))
        config = _make_config()
        engine = LiveTradingEngine(client, strategy, config)

        result = await engine.run(max_ticks=3)

        expected_snapshots = 3
        assert result.snapshots_processed == expected_snapshots

    @pytest.mark.asyncio
    async def test_fetch_failure_skips_market(self) -> None:
        """Verify a fetch failure skips the market gracefully."""
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
        engine = LiveTradingEngine(client, strategy, config)

        result = await engine.run(max_ticks=2)

        assert result.snapshots_processed == 0

    @pytest.mark.asyncio
    async def test_signal_triggers_trade(self) -> None:
        """Verify a strategy signal results in a live trade."""
        client = _mock_client()
        prices = ["0.60", "0.60", "0.60", "0.60", "0.60", "0.60", "0.40"]
        call_count = 0

        async def varying_market(condition_id: str) -> Market:  # noqa: ARG001
            nonlocal call_count
            idx = min(call_count, len(prices) - 1)
            call_count += 1
            return _make_market(
                yes_price=prices[idx],
                no_price=str(Decimal(1) - Decimal(prices[idx])),
            )

        client.get_market = varying_market  # type: ignore[assignment]
        strategy = PMMeanReversionStrategy(period=5, z_threshold=Decimal("1.5"))
        config = _make_config()
        engine = LiveTradingEngine(client, strategy, config)

        result = await engine.run(max_ticks=len(prices))

        buy_trades = [t for t in result.trades if t.side == Side.BUY]
        assert len(buy_trades) > 0

    @pytest.mark.asyncio
    async def test_sell_signal_closes_position(self) -> None:
        """Verify a SELL signal closes an existing position."""
        client = _mock_client()
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
                yes_price=prices[idx],
                no_price=str(Decimal(1) - Decimal(prices[idx])),
            )

        client.get_market = varying_market  # type: ignore[assignment]
        strategy = PMMeanReversionStrategy(period=5, z_threshold=Decimal("1.5"))
        config = _make_config()
        engine = LiveTradingEngine(client, strategy, config)

        result = await engine.run(max_ticks=len(prices))

        sell_trades = [t for t in result.trades if t.side == Side.SELL]
        assert len(sell_trades) > 0

    @pytest.mark.asyncio
    async def test_loss_limit_stops_engine(self) -> None:
        """Verify the engine stops when loss limit is breached."""
        client = _mock_client()
        # Start with $1000, lose 15% → equity $850
        # Set max_loss_pct=0.10, so limit at $900
        low_balance = Decimal("850.00")

        balance_calls = [_INITIAL_BALANCE, low_balance]
        call_idx = 0

        async def varying_balance(asset_type: str) -> Balance:  # noqa: ARG001
            nonlocal call_idx
            bal = balance_calls[min(call_idx, len(balance_calls) - 1)]
            call_idx += 1
            return Balance(asset_type="COLLATERAL", balance=bal, allowance=Decimal(10000))

        client.get_balance = varying_balance  # type: ignore[assignment]

        strategy = PMMeanReversionStrategy(period=3, z_threshold=Decimal("1.5"))
        config = _make_config()
        engine = LiveTradingEngine(
            client,
            strategy,
            config,
            max_loss_pct=Decimal("0.10"),
        )

        result = await engine.run(max_ticks=10)

        # Should stop early due to loss limit
        max_expected_ticks = 3
        assert result.snapshots_processed <= max_expected_ticks

    @pytest.mark.asyncio
    async def test_shutdown_flag_stops_engine(self) -> None:
        """Verify setting _shutdown flag stops the engine."""
        client = _mock_client()
        strategy = PMMeanReversionStrategy(period=3, z_threshold=Decimal("1.5"))
        config = _make_config()
        engine = LiveTradingEngine(client, strategy, config)

        # Set shutdown before running
        engine._shutdown = True

        result = await engine.run(max_ticks=100)

        assert result.snapshots_processed == 0

    @pytest.mark.asyncio
    async def test_trade_opened_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        """Verify a successful trade open is logged with order details."""
        client = _mock_client()
        prices = ["0.60", "0.60", "0.60", "0.60", "0.60", "0.60", "0.40"]
        call_count = 0

        async def varying_market(condition_id: str) -> Market:  # noqa: ARG001
            nonlocal call_count
            idx = min(call_count, len(prices) - 1)
            call_count += 1
            return _make_market(
                yes_price=prices[idx],
                no_price=str(Decimal(1) - Decimal(prices[idx])),
            )

        client.get_market = varying_market  # type: ignore[assignment]
        strategy = PMMeanReversionStrategy(period=5, z_threshold=Decimal("1.5"))
        config = _make_config()
        engine = LiveTradingEngine(client, strategy, config)

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
        config = BotConfig(poll_interval_seconds=0, markets=())
        engine = LiveTradingEngine(client, strategy, config)

        result = await engine.run(max_ticks=3)

        assert result.snapshots_processed == 0
        assert result.trades == ()

    @pytest.mark.asyncio
    async def test_token_ids_cached_from_market(self) -> None:
        """Verify token IDs are cached after fetching market data."""
        client = _mock_client()
        strategy = PMMeanReversionStrategy(period=3, z_threshold=Decimal("1.5"))
        config = _make_config()
        engine = LiveTradingEngine(client, strategy, config)

        await engine.run(max_ticks=1)

        assert _CONDITION_ID in engine._token_ids
        assert engine._token_ids[_CONDITION_ID] == (_YES_TOKEN_ID, _NO_TOKEN_ID)


_NEW_CONDITION_ID = "cond_rotated_live_market"


class TestLiveMarketRotation:
    """Tests for 5-minute market rotation in live engine."""

    @pytest.mark.asyncio
    async def test_rotation_discovers_new_markets(self) -> None:
        """Verify window change triggers market re-discovery."""
        start_time = 1700000000
        tick_time = start_time + 300

        client = _mock_client()
        client.discover_series_markets = AsyncMock(
            return_value=[(_NEW_CONDITION_ID, "2026-02-22T12:10:00Z")],
        )

        config = BotConfig(
            poll_interval_seconds=0,
            max_position_pct=Decimal("0.1"),
            kelly_fraction=Decimal("0.25"),
            max_history=100,
            markets=(_CONDITION_ID,),
            series_slugs=("btc-updown-5m",),
        )

        time_calls = iter([float(start_time), float(tick_time), float(tick_time)])

        with patch("trading_tools.apps.polymarket_bot.live_engine.time") as mock_time:
            mock_time.time.side_effect = time_calls

            engine = LiveTradingEngine(
                client,
                strategy=PMMeanReversionStrategy(),
                config=config,
            )
            engine._current_window = (start_time // 300) * 300

            await engine._tick()

        client.discover_series_markets.assert_called_once_with(["btc-updown-5m"])
        assert _NEW_CONDITION_ID in engine._active_markets

    @pytest.mark.asyncio
    async def test_no_rotation_without_series_slugs(self) -> None:
        """Verify rotation is skipped when series_slugs is empty."""
        client = _mock_client()
        client.discover_series_markets = AsyncMock()

        config = _make_config()
        engine = LiveTradingEngine(
            client,
            strategy=PMMeanReversionStrategy(),
            config=config,
        )

        await engine.run(max_ticks=2)

        client.discover_series_markets.assert_not_called()

    @pytest.mark.asyncio
    async def test_rotation_emits_perf_log(self, caplog: pytest.LogCaptureFixture) -> None:
        """Verify performance metrics are logged after market rotation."""
        start_time = 1700000000
        tick_time = start_time + 300

        client = _mock_client()
        client.discover_series_markets = AsyncMock(
            return_value=[(_NEW_CONDITION_ID, "2026-02-22T12:10:00Z")],
        )

        config = BotConfig(
            poll_interval_seconds=0,
            max_position_pct=Decimal("0.1"),
            kelly_fraction=Decimal("0.25"),
            max_history=100,
            markets=(_CONDITION_ID,),
            series_slugs=("btc-updown-5m",),
        )

        time_calls = iter([float(start_time), float(tick_time), float(tick_time)])

        with patch("trading_tools.apps.polymarket_bot.live_engine.time") as mock_time:
            mock_time.time.side_effect = time_calls

            engine = LiveTradingEngine(
                client,
                strategy=PMMeanReversionStrategy(),
                config=config,
            )
            engine._current_window = (start_time // 300) * 300

            with caplog.at_level(
                logging.INFO,
                logger="trading_tools.apps.polymarket_bot.live_engine",
            ):
                await engine._tick()

        perf_messages = [msg for msg in caplog.messages if "[PERF" in msg]
        assert len(perf_messages) == 1
