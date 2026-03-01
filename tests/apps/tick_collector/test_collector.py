"""Tests for the tick collector orchestrator."""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trading_tools.apps.tick_collector.collector import (
    TickCollector,
    _now_ms,
    _seconds_until_next_discovery,
)
from trading_tools.apps.tick_collector.config import CollectorConfig
from trading_tools.clients.polymarket.models import Market, MarketToken

_CONDITION_ID = "cond_test_123"
_ASSET_ID_YES = "token_yes_456"
_ASSET_ID_NO = "token_no_789"
_EXPECTED_PRICE = 0.72
_EXPECTED_SIZE = 10.0
_TICK_COUNT_2 = 2
_TICK_COUNT_3 = 3
_MIN_EPOCH_MS = 1_000_000_000_000

_SAMPLE_MARKET = Market(
    condition_id=_CONDITION_ID,
    question="Will BTC hit $100K?",
    description="Test market",
    tokens=(
        MarketToken(token_id=_ASSET_ID_YES, outcome="Yes", price=Decimal("0.72")),
        MarketToken(token_id=_ASSET_ID_NO, outcome="No", price=Decimal("0.28")),
    ),
    end_date="2026-03-31",
    volume=Decimal(50000),
    liquidity=Decimal(10000),
    active=True,
)


def _make_config(
    *,
    db_url: str = "sqlite+aiosqlite:///:memory:",
    markets: tuple[str, ...] = (_CONDITION_ID,),
    series_slugs: tuple[str, ...] = (),
    flush_batch_size: int = 2,
    flush_interval_seconds: int = 100,
    discovery_interval_seconds: int = 100,
    discovery_lead_seconds: int = 30,
) -> CollectorConfig:
    """Create a CollectorConfig for testing.

    Args:
        db_url: Database connection string.
        markets: Static condition IDs.
        series_slugs: Series slugs for discovery.
        flush_batch_size: Batch size before forced flush.
        flush_interval_seconds: Timer-based flush interval.
        discovery_interval_seconds: Market re-discovery interval.
        discovery_lead_seconds: Seconds before next boundary to discover.

    Returns:
        CollectorConfig with test parameters.

    """
    return CollectorConfig(
        db_url=db_url,
        markets=markets,
        series_slugs=series_slugs,
        flush_batch_size=flush_batch_size,
        flush_interval_seconds=flush_interval_seconds,
        discovery_interval_seconds=discovery_interval_seconds,
        discovery_lead_seconds=discovery_lead_seconds,
    )


def _make_trade_event(
    asset_id: str = _ASSET_ID_YES,
    price: str = "0.72",
    size: str = "10.0",
) -> dict[str, Any]:
    """Create a sample trade event dict.

    Args:
        asset_id: Token identifier.
        price: Trade price as string.
        size: Trade size as string.

    Returns:
        Event dictionary matching WebSocket format.

    """
    return {
        "event_type": "last_trade_price",
        "asset_id": asset_id,
        "price": price,
        "size": size,
        "side": "BUY",
        "fee_rate_bps": 200,
        "timestamp": 1700000000000,
    }


def _mock_polymarket_client() -> AsyncMock:
    """Create a mock PolymarketClient that returns a sample market.

    Returns:
        AsyncMock configured as a PolymarketClient.

    """
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get_market = AsyncMock(return_value=_SAMPLE_MARKET)
    mock_client.get_market_tokens = AsyncMock(return_value=_SAMPLE_MARKET)
    mock_client.discover_series_markets = AsyncMock(return_value=[])
    return mock_client


class TestTickCollectorHandleEvent:
    """Tests for event handling and buffering."""

    def test_handle_event_adds_to_buffer(self) -> None:
        """Verify that a valid event is buffered as a Tick."""
        config = _make_config()
        collector = TickCollector(config)
        collector._condition_map[_ASSET_ID_YES] = _CONDITION_ID

        collector._handle_event(_make_trade_event())

        assert len(collector._buffer) == 1
        tick = collector._buffer[0]
        assert tick.asset_id == _ASSET_ID_YES
        assert tick.condition_id == _CONDITION_ID
        assert tick.price == _EXPECTED_PRICE
        assert tick.size == _EXPECTED_SIZE
        assert tick.side == "BUY"

    def test_handle_event_increments_counters(self) -> None:
        """Verify tick counters are incremented on each event."""
        config = _make_config()
        collector = TickCollector(config)

        collector._handle_event(_make_trade_event())
        collector._handle_event(_make_trade_event())

        assert collector._ticks_since_heartbeat == _TICK_COUNT_2
        assert collector._total_ticks == _TICK_COUNT_2

    def test_handle_malformed_event(self) -> None:
        """Malformed events with unconvertible values are skipped without error."""
        config = _make_config()
        collector = TickCollector(config)

        collector._handle_event({"price": "not_a_number", "size": object()})

        assert len(collector._buffer) == 0


class TestTickCollectorFlush:
    """Tests for buffer flushing."""

    @pytest.mark.asyncio
    async def test_flush_buffer_saves_ticks(self) -> None:
        """Verify flush writes buffered ticks to the repository."""
        config = _make_config()
        collector = TickCollector(config)
        collector._repo = MagicMock()
        collector._repo.save_ticks = AsyncMock()
        collector._condition_map[_ASSET_ID_YES] = _CONDITION_ID

        collector._handle_event(_make_trade_event())
        collector._handle_event(_make_trade_event())
        await collector._flush_buffer()

        collector._repo.save_ticks.assert_awaited_once()
        saved_ticks = collector._repo.save_ticks.call_args[0][0]
        assert len(saved_ticks) == _TICK_COUNT_2

    @pytest.mark.asyncio
    async def test_flush_empty_buffer_no_op(self) -> None:
        """Flushing an empty buffer does not call save_ticks."""
        config = _make_config()
        collector = TickCollector(config)
        collector._repo = MagicMock()
        collector._repo.save_ticks = AsyncMock()

        await collector._flush_buffer()

        collector._repo.save_ticks.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_flush_clears_buffer(self) -> None:
        """Buffer is empty after flushing."""
        config = _make_config()
        collector = TickCollector(config)
        collector._repo = MagicMock()
        collector._repo.save_ticks = AsyncMock()

        collector._handle_event(_make_trade_event())
        await collector._flush_buffer()

        assert len(collector._buffer) == 0


class TestTickCollectorEndToEnd:
    """End-to-end tests for the collector run loop."""

    @pytest.mark.asyncio
    async def test_run_processes_events_and_flushes(self) -> None:
        """Verify the run loop processes WebSocket events into the database."""
        config = _make_config(flush_batch_size=2)

        events = [
            _make_trade_event(price="0.70"),
            _make_trade_event(price="0.71"),
            _make_trade_event(price="0.72"),
        ]

        async def _mock_stream(asset_ids: list[str]) -> Any:  # noqa: ARG001
            for event in events:
                yield event

        mock_client = _mock_polymarket_client()

        with patch(
            "trading_tools.apps.tick_collector.collector.PolymarketClient",
            return_value=mock_client,
        ):
            collector = TickCollector(config)

            mock_feed = AsyncMock()
            mock_feed.stream = _mock_stream
            mock_feed.close = AsyncMock()
            mock_feed.update_subscription = AsyncMock()
            collector._feed = mock_feed

            mock_repo = MagicMock()
            mock_repo.init_db = AsyncMock()
            mock_repo.save_ticks = AsyncMock()
            mock_repo.get_tick_count = AsyncMock(return_value=3)
            mock_repo.close = AsyncMock()
            collector._repo = mock_repo

            # Pre-populate the asset IDs and condition map
            collector._asset_ids = [_ASSET_ID_YES, _ASSET_ID_NO]
            collector._condition_map = {
                _ASSET_ID_YES: _CONDITION_ID,
                _ASSET_ID_NO: _CONDITION_ID,
            }

            # Patch signal handlers (not available in test context)
            with patch("asyncio.get_running_loop") as mock_loop:
                mock_loop.return_value = MagicMock()
                # Patch _discover_and_resolve to avoid real API calls
                collector._discover_and_resolve = AsyncMock()  # type: ignore[method-assign]
                collector._discover_and_resolve.return_value = None

                await collector.run()

        # Batch size is 2, so at least one flush happened mid-stream
        assert mock_repo.save_ticks.await_count >= 1
        assert collector._total_ticks == _TICK_COUNT_3

    @pytest.mark.asyncio
    async def test_shutdown_flushes_remaining(self) -> None:
        """Verify shutdown flushes any remaining buffered ticks."""
        config = _make_config(flush_batch_size=100)

        async def _mock_stream(asset_ids: list[str]) -> Any:  # noqa: ARG001
            yield _make_trade_event()
            # Simulate shutdown after one event
            collector._shutdown = True

        mock_client = _mock_polymarket_client()

        with patch(
            "trading_tools.apps.tick_collector.collector.PolymarketClient",
            return_value=mock_client,
        ):
            collector = TickCollector(config)

            mock_feed = AsyncMock()
            mock_feed.stream = _mock_stream
            mock_feed.close = AsyncMock()
            collector._feed = mock_feed

            mock_repo = MagicMock()
            mock_repo.init_db = AsyncMock()
            mock_repo.save_ticks = AsyncMock()
            mock_repo.get_tick_count = AsyncMock(return_value=1)
            mock_repo.close = AsyncMock()
            collector._repo = mock_repo

            collector._asset_ids = [_ASSET_ID_YES]
            collector._condition_map = {_ASSET_ID_YES: _CONDITION_ID}

            with patch("asyncio.get_running_loop") as mock_loop:
                mock_loop.return_value = MagicMock()
                collector._discover_and_resolve = AsyncMock()  # type: ignore[method-assign]

                await collector.run()

        # Final flush should have been called with the remaining tick
        mock_repo.save_ticks.assert_awaited()


class TestTickCollectorDiscovery:
    """Tests for market discovery and asset resolution."""

    @pytest.mark.asyncio
    async def test_discover_resolves_condition_to_assets(self) -> None:
        """Verify discovery maps condition IDs to asset IDs."""
        config = _make_config(markets=(_CONDITION_ID,))

        mock_client = _mock_polymarket_client()

        with patch(
            "trading_tools.apps.tick_collector.collector.PolymarketClient",
            return_value=mock_client,
        ):
            collector = TickCollector(config)
            await collector._discover_and_resolve()

        assert _ASSET_ID_YES in collector._asset_ids
        assert _ASSET_ID_NO in collector._asset_ids
        assert collector._condition_map[_ASSET_ID_YES] == _CONDITION_ID
        assert collector._condition_map[_ASSET_ID_NO] == _CONDITION_ID

    @pytest.mark.asyncio
    async def test_discover_with_series_slugs(self) -> None:
        """Verify series slug discovery adds discovered condition IDs."""
        config = _make_config(
            markets=(),
            series_slugs=("btc-updown-5m",),
        )

        discovered_cid = "cond_discovered"
        discovered_market = Market(
            condition_id=discovered_cid,
            question="Discovered market",
            description="",
            tokens=(
                MarketToken(
                    token_id="tok_disc_yes",
                    outcome="Yes",
                    price=Decimal("0.60"),
                ),
            ),
            end_date="2026-04-01",
            volume=Decimal(1000),
            liquidity=Decimal(500),
            active=True,
        )

        mock_client = _mock_polymarket_client()
        mock_client.discover_series_markets = AsyncMock(
            return_value=[(discovered_cid, "2026-04-01")]
        )
        mock_client.get_market_tokens = AsyncMock(return_value=discovered_market)

        with patch(
            "trading_tools.apps.tick_collector.collector.PolymarketClient",
            return_value=mock_client,
        ):
            collector = TickCollector(config)
            await collector._discover_and_resolve()

        assert "tok_disc_yes" in collector._asset_ids

    @pytest.mark.asyncio
    async def test_discover_calls_get_market_tokens(self) -> None:
        """Verify _discover_and_resolve uses get_market_tokens, not get_market."""
        config = _make_config(markets=(_CONDITION_ID,))

        mock_client = _mock_polymarket_client()

        with patch(
            "trading_tools.apps.tick_collector.collector.PolymarketClient",
            return_value=mock_client,
        ):
            collector = TickCollector(config)
            await collector._discover_and_resolve()

        mock_client.get_market_tokens.assert_awaited()
        mock_client.get_market.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_discover_passes_include_next(self) -> None:
        """Verify discover_series_markets is called with include_next=True."""
        config = _make_config(
            markets=(),
            series_slugs=("btc-updown-5m",),
        )

        mock_client = _mock_polymarket_client()

        with patch(
            "trading_tools.apps.tick_collector.collector.PolymarketClient",
            return_value=mock_client,
        ):
            collector = TickCollector(config)
            await collector._discover_and_resolve()

        mock_client.discover_series_markets.assert_awaited_once_with(
            ["btc-updown-5m"], include_next=True
        )

    @pytest.mark.asyncio
    async def test_discover_parallel_resolution(self) -> None:
        """Verify multiple condition IDs are resolved concurrently."""
        cid_a = "cond_a"
        cid_b = "cond_b"
        config = _make_config(markets=(cid_a, cid_b))

        market_a = Market(
            condition_id=cid_a,
            question="A",
            description="",
            tokens=(MarketToken(token_id="tok_a", outcome="Yes", price=Decimal("0.5")),),
            end_date="",
            volume=Decimal(0),
            liquidity=Decimal(0),
            active=True,
        )
        market_b = Market(
            condition_id=cid_b,
            question="B",
            description="",
            tokens=(MarketToken(token_id="tok_b", outcome="Yes", price=Decimal("0.5")),),
            end_date="",
            volume=Decimal(0),
            liquidity=Decimal(0),
            active=True,
        )

        mock_client = _mock_polymarket_client()

        async def _mock_get_tokens(cid: str) -> Market:
            return market_a if cid == cid_a else market_b

        mock_client.get_market_tokens = AsyncMock(side_effect=_mock_get_tokens)

        with patch(
            "trading_tools.apps.tick_collector.collector.PolymarketClient",
            return_value=mock_client,
        ):
            collector = TickCollector(config)
            await collector._discover_and_resolve()

        assert "tok_a" in collector._asset_ids
        assert "tok_b" in collector._asset_ids
        assert mock_client.get_market_tokens.await_count == _TICK_COUNT_2


class TestNowMs:
    """Tests for the _now_ms utility."""

    def test_returns_positive_integer(self) -> None:
        """Verify _now_ms returns a positive integer."""
        result = _now_ms()
        assert isinstance(result, int)
        assert result > 0

    def test_returns_milliseconds(self) -> None:
        """Verify the value is in milliseconds (> 1e12 for modern epochs)."""
        result = _now_ms()
        assert result > _MIN_EPOCH_MS


class TestHandleShutdown:
    """Tests for the shutdown signal handler."""

    def test_sets_shutdown_flag(self) -> None:
        """Verify _handle_shutdown sets the shutdown flag."""
        config = _make_config()
        collector = TickCollector(config)

        collector._handle_shutdown()

        assert collector._shutdown is True


_FIVE_MINUTES = 300


class TestWindowAlignedDiscovery:
    """Tests for the window-aligned discovery sleep helper."""

    def test_seconds_until_next_discovery(self) -> None:
        """Compute correct sleep duration at various points in a window."""
        lead = 30
        # 1 minute into a window → next fire at 4m30s → sleep 210s
        now = 1_000_000_000 * _FIVE_MINUTES + 60
        expected = _FIVE_MINUTES - lead - 60
        assert _seconds_until_next_discovery(now, lead) == expected

        # Exactly at window start → sleep 270s (5m - 30s)
        now_start = 1_000_000_000 * _FIVE_MINUTES
        assert _seconds_until_next_discovery(now_start, lead) == _FIVE_MINUTES - lead

    def test_seconds_until_next_discovery_past_fire_time(self) -> None:
        """Sleep until next window's fire time when past current fire time."""
        lead = 30
        # 4m45s into window → past 4m30s fire time → sleep until next window
        # fire_at=270, elapsed=285, remaining=270-285=-15, result=-15+300=285
        now = 1_000_000_000 * _FIVE_MINUTES + 285
        expected_sleep = 285
        assert _seconds_until_next_discovery(now, lead) == expected_sleep


class TestRunNoAssets:
    """Tests for the run loop when no assets are discovered."""

    @pytest.mark.asyncio
    async def test_run_returns_early_when_no_assets(self) -> None:
        """Return immediately when no asset IDs are discovered."""
        config = _make_config(markets=())

        mock_client = _mock_polymarket_client()
        mock_client.get_market_tokens = AsyncMock(
            return_value=Market(
                condition_id="empty",
                question="",
                description="",
                tokens=(),
                end_date="",
                volume=Decimal(0),
                liquidity=Decimal(0),
                active=True,
            )
        )

        with patch(
            "trading_tools.apps.tick_collector.collector.PolymarketClient",
            return_value=mock_client,
        ):
            collector = TickCollector(config)
            mock_repo = MagicMock()
            mock_repo.init_db = AsyncMock()
            mock_repo.close = AsyncMock()
            collector._repo = mock_repo

            with patch("asyncio.get_running_loop") as mock_loop:
                mock_loop.return_value = MagicMock()
                await collector.run()

        # Should not have started the feed stream
        assert collector._total_ticks == 0


class TestDiscoveryFailure:
    """Tests for discovery error handling."""

    @pytest.mark.asyncio
    async def test_series_discovery_failure_continues(self) -> None:
        """Continue with static markets when series discovery fails."""
        config = _make_config(
            markets=(_CONDITION_ID,),
            series_slugs=("btc-updown-5m",),
        )

        mock_client = _mock_polymarket_client()
        mock_client.discover_series_markets = AsyncMock(side_effect=Exception("API unavailable"))

        with patch(
            "trading_tools.apps.tick_collector.collector.PolymarketClient",
            return_value=mock_client,
        ):
            collector = TickCollector(config)
            await collector._discover_and_resolve()

        # Static market was still resolved
        assert _ASSET_ID_YES in collector._asset_ids

    @pytest.mark.asyncio
    async def test_resolve_one_failure_returns_empty(self) -> None:
        """Continue when resolving a single market fails."""
        config = _make_config(markets=(_CONDITION_ID,))

        mock_client = _mock_polymarket_client()
        mock_client.get_market_tokens = AsyncMock(side_effect=Exception("Market not found"))

        with patch(
            "trading_tools.apps.tick_collector.collector.PolymarketClient",
            return_value=mock_client,
        ):
            collector = TickCollector(config)
            await collector._discover_and_resolve()

        # No assets resolved since the API call failed
        assert len(collector._asset_ids) == 0


class TestPeriodicTasks:
    """Tests for the periodic background tasks."""

    @pytest.mark.asyncio
    async def test_periodic_discovery_discovers_and_updates(self) -> None:
        """Verify periodic discovery runs and updates subscription."""
        config = _make_config(
            markets=(_CONDITION_ID,),
            series_slugs=("test-series",),
            discovery_lead_seconds=30,
        )

        mock_client = _mock_polymarket_client()

        with patch(
            "trading_tools.apps.tick_collector.collector.PolymarketClient",
            return_value=mock_client,
        ):
            collector = TickCollector(config)
            collector._asset_ids = [_ASSET_ID_YES]
            collector._condition_map = {_ASSET_ID_YES: _CONDITION_ID}

            mock_feed = MagicMock()
            mock_feed.update_subscription = AsyncMock()
            collector._feed = mock_feed

            # Make discover_and_resolve add a new asset
            async def mock_discover() -> None:
                collector._asset_ids.append("new_asset")
                collector._condition_map["new_asset"] = "new_cond"

            collector._discover_and_resolve = mock_discover  # type: ignore[method-assign]

            # Patch sleep to run once then shut down
            call_count = 0

            async def fast_sleep(delay: float) -> None:  # noqa: ARG001
                nonlocal call_count
                call_count += 1
                if call_count > 1:
                    collector._shutdown = True

            with patch("asyncio.sleep", side_effect=fast_sleep):
                await collector._periodic_discovery()

            mock_feed.update_subscription.assert_awaited()

    @pytest.mark.asyncio
    async def test_periodic_heartbeat_logs_stats(self, caplog: pytest.LogCaptureFixture) -> None:
        """Verify heartbeat logs tick stats and resets counter."""
        config = _make_config()

        collector = TickCollector(config)
        collector._ticks_since_heartbeat = 42
        collector._asset_ids = [_ASSET_ID_YES]

        mock_repo = MagicMock()
        mock_repo.get_tick_count = AsyncMock(return_value=100)
        collector._repo = mock_repo

        call_count = 0

        async def fast_sleep(delay: float) -> None:  # noqa: ARG001
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                collector._shutdown = True

        with (
            patch("asyncio.sleep", side_effect=fast_sleep),
            caplog.at_level(logging.INFO),
        ):
            await collector._periodic_heartbeat()

        assert collector._ticks_since_heartbeat == 0
        assert "TICK-COLLECTOR" in caplog.text

    @pytest.mark.asyncio
    async def test_periodic_flush_flushes_buffer(self) -> None:
        """Verify periodic flush writes buffered ticks."""
        config = _make_config(flush_interval_seconds=1)

        collector = TickCollector(config)
        collector._condition_map[_ASSET_ID_YES] = _CONDITION_ID
        collector._handle_event(_make_trade_event())
        # Set last flush time far in the past to trigger flush
        collector._last_flush_time = 0.0

        mock_repo = MagicMock()
        mock_repo.save_ticks = AsyncMock()
        collector._repo = mock_repo

        call_count = 0

        async def fast_sleep(delay: float) -> None:  # noqa: ARG001
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                collector._shutdown = True

        with patch("asyncio.sleep", side_effect=fast_sleep):
            await collector._periodic_flush()

        mock_repo.save_ticks.assert_awaited()

    @pytest.mark.asyncio
    async def test_periodic_flush_skips_empty_buffer(self) -> None:
        """Verify periodic flush does not write when buffer is empty."""
        config = _make_config(flush_interval_seconds=1)

        collector = TickCollector(config)
        collector._last_flush_time = 0.0

        mock_repo = MagicMock()
        mock_repo.save_ticks = AsyncMock()
        collector._repo = mock_repo

        call_count = 0

        async def fast_sleep(delay: float) -> None:  # noqa: ARG001
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                collector._shutdown = True

        with patch("asyncio.sleep", side_effect=fast_sleep):
            await collector._periodic_flush()

        mock_repo.save_ticks.assert_not_awaited()
