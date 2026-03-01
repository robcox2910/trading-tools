"""Tests for the BaseTradingEngine shared event-loop infrastructure."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trading_tools.apps.polymarket_bot.base_engine import BaseTradingEngine
from trading_tools.apps.polymarket_bot.base_portfolio import BasePortfolio
from trading_tools.apps.polymarket_bot.models import BotConfig, MarketSnapshot
from trading_tools.clients.polymarket.exceptions import PolymarketAPIError
from trading_tools.clients.polymarket.models import Market, MarketToken, OrderBook, OrderLevel
from trading_tools.core.models import Position, Side, Signal

_CONDITION_ID = "cond_base_engine_test"
_YES_TOKEN_ID = "yes_tok_base"
_NO_TOKEN_ID = "no_tok_base"
_MIN_TOKENS = 2


class _StubPortfolio(BasePortfolio):
    """Minimal portfolio for testing."""

    def __init__(self) -> None:
        """Initialize with fixed defaults."""
        super().__init__(Decimal("0.25"))
        self._cash = Decimal(1000)

    def _get_cash_balance(self) -> Decimal:
        """Return the fixed cash balance."""
        return self._cash


class _ConcreteEngine(BaseTradingEngine[_StubPortfolio]):
    """Minimal concrete engine for testing base class methods."""

    def __init__(
        self,
        client: Any,
        config: BotConfig,
        portfolio: _StubPortfolio | None = None,
        feed: Any = None,
    ) -> None:
        """Initialize with a mock strategy."""
        strategy = MagicMock()
        strategy.name = "test_strategy"
        strategy.on_snapshot = MagicMock(return_value=None)
        port = portfolio or _StubPortfolio()
        super().__init__(client, strategy, config, port, feed)
        self.applied_signals: list[tuple[Signal, MarketSnapshot]] = []
        self.rotation_close_calls = 0
        self.performance_log_calls = 0

    async def _apply_signal(self, signal: Signal, snapshot: MarketSnapshot) -> None:
        """Record signals for assertion."""
        self.applied_signals.append((signal, snapshot))

    async def _on_rotation_close(self) -> None:
        """Track rotation close calls."""
        self.rotation_close_calls += 1

    def _log_performance(self) -> None:
        """Track performance log calls."""
        self.performance_log_calls += 1


def _make_market(
    condition_id: str = _CONDITION_ID,
    yes_price: Decimal = Decimal("0.60"),
    no_price: Decimal = Decimal("0.40"),
) -> Market:
    """Create a test Market with two tokens."""
    return Market(
        condition_id=condition_id,
        question="Will X happen?",
        description="Test market",
        tokens=(
            MarketToken(token_id=_YES_TOKEN_ID, outcome="Yes", price=yes_price),
            MarketToken(token_id=_NO_TOKEN_ID, outcome="No", price=no_price),
        ),
        end_date="2026-12-31",
        volume=Decimal(50000),
        liquidity=Decimal(10000),
        active=True,
    )


def _make_order_book() -> OrderBook:
    """Create a test OrderBook with basic bid/ask."""
    return OrderBook(
        token_id=_YES_TOKEN_ID,
        bids=(OrderLevel(price=Decimal("0.59"), size=Decimal(100)),),
        asks=(OrderLevel(price=Decimal("0.61"), size=Decimal(100)),),
        spread=Decimal("0.02"),
        midpoint=Decimal("0.60"),
    )


def _make_config(
    markets: tuple[str, ...] = (_CONDITION_ID,),
    series_slugs: tuple[str, ...] = (),
) -> BotConfig:
    """Create a test BotConfig."""
    return BotConfig(
        markets=markets,
        order_book_refresh_seconds=30,
        initial_capital=Decimal(1000),
        max_position_pct=Decimal("0.25"),
        kelly_fraction=Decimal("0.25"),
        max_history=50,
        series_slugs=series_slugs,
    )


def _make_ws_event(
    asset_id: str = _YES_TOKEN_ID,
    price: str = "0.60",
) -> dict[str, Any]:
    """Create a simulated WebSocket trade event."""
    return {"asset_id": asset_id, "price": price}


def _mock_client() -> AsyncMock:
    """Create a mock PolymarketClient."""
    client = AsyncMock()
    client.get_market = AsyncMock(return_value=_make_market())
    client.get_order_book = AsyncMock(return_value=_make_order_book())
    client.discover_series_markets = AsyncMock(return_value=[])
    return client


class TestBootstrapMarket:
    """Tests for _bootstrap_market helper."""

    @pytest.mark.asyncio
    async def test_registers_market_and_tokens(self) -> None:
        """Verify successful bootstrap registers market in tracker."""
        client = _mock_client()
        engine = _ConcreteEngine(client, _make_config())

        market = await engine._bootstrap_market(_CONDITION_ID)

        assert market is not None
        assert _CONDITION_ID in engine._cached_markets
        assert _YES_TOKEN_ID in engine._asset_ids
        assert _NO_TOKEN_ID in engine._asset_ids

    @pytest.mark.asyncio
    async def test_returns_none_for_insufficient_tokens(self) -> None:
        """Return None when market has fewer than 2 tokens."""
        one_token_market = Market(
            condition_id=_CONDITION_ID,
            question="Test",
            description="",
            tokens=(MarketToken(token_id="only_one", outcome="Yes", price=Decimal("0.5")),),
            end_date="2026-12-31",
            volume=Decimal(0),
            liquidity=Decimal(0),
            active=True,
        )
        client = _mock_client()
        client.get_market.return_value = one_token_market
        engine = _ConcreteEngine(client, _make_config())

        result = await engine._bootstrap_market(_CONDITION_ID)

        assert result is None
        assert _CONDITION_ID not in engine._cached_markets

    @pytest.mark.asyncio
    async def test_calls_on_bootstrap_market_hook(self) -> None:
        """Verify the _on_bootstrap_market hook is called."""
        client = _mock_client()
        engine = _ConcreteEngine(client, _make_config())
        engine._on_bootstrap_market = MagicMock()  # type: ignore[method-assign]

        await engine._bootstrap_market(_CONDITION_ID)

        engine._on_bootstrap_market.assert_called_once()


class TestBootstrap:
    """Tests for _bootstrap."""

    @pytest.mark.asyncio
    async def test_bootstraps_all_markets(self) -> None:
        """Verify bootstrap registers markets and fetches order books."""
        client = _mock_client()
        engine = _ConcreteEngine(client, _make_config())

        await engine._bootstrap()

        assert _CONDITION_ID in engine._cached_markets
        assert _CONDITION_ID in engine._cached_order_books

    @pytest.mark.asyncio
    async def test_bootstrap_skips_failed_market(self) -> None:
        """Continue bootstrapping when one market fails."""
        client = _mock_client()
        client.get_market.side_effect = PolymarketAPIError(msg="API down", status_code=0)
        engine = _ConcreteEngine(client, _make_config())

        await engine._bootstrap()

        assert len(engine._cached_markets) == 0

    @pytest.mark.asyncio
    async def test_bootstrap_skips_failed_order_book(self) -> None:
        """Continue when order book fetch fails but market succeeds."""
        client = _mock_client()
        client.get_order_book.side_effect = PolymarketAPIError(msg="OB unavailable", status_code=0)
        engine = _ConcreteEngine(client, _make_config())

        await engine._bootstrap()

        assert _CONDITION_ID in engine._cached_markets
        assert _CONDITION_ID not in engine._cached_order_books


class TestBuildSnapshot:
    """Tests for _build_snapshot."""

    @pytest.mark.asyncio
    async def test_builds_valid_snapshot(self) -> None:
        """Build a snapshot when all data is available."""
        client = _mock_client()
        engine = _ConcreteEngine(client, _make_config())
        await engine._bootstrap()

        snapshot = engine._build_snapshot(_CONDITION_ID)

        assert snapshot is not None
        assert snapshot.condition_id == _CONDITION_ID
        assert snapshot.yes_price == Decimal("0.60")

    def test_returns_none_missing_prices(self) -> None:
        """Return None when price tracker has no prices."""
        engine = _ConcreteEngine(_mock_client(), _make_config())

        snapshot = engine._build_snapshot(_CONDITION_ID)

        assert snapshot is None

    @pytest.mark.asyncio
    async def test_returns_none_missing_order_book(self) -> None:
        """Return None when order book is not cached."""
        client = _mock_client()
        engine = _ConcreteEngine(client, _make_config())
        await engine._bootstrap()
        del engine._cached_order_books[_CONDITION_ID]

        snapshot = engine._build_snapshot(_CONDITION_ID)

        assert snapshot is None

    @pytest.mark.asyncio
    async def test_returns_none_missing_market(self) -> None:
        """Return None when market metadata is not cached."""
        client = _mock_client()
        engine = _ConcreteEngine(client, _make_config())
        await engine._bootstrap()
        del engine._cached_markets[_CONDITION_ID]

        snapshot = engine._build_snapshot(_CONDITION_ID)

        assert snapshot is None


class TestOnPriceUpdate:
    """Tests for _on_price_update."""

    @pytest.mark.asyncio
    async def test_processes_valid_event(self) -> None:
        """Process a valid trade event and increment snapshot counter."""
        client = _mock_client()
        engine = _ConcreteEngine(client, _make_config())
        await engine._bootstrap()

        await engine._on_price_update(_make_ws_event())

        assert engine._snapshots_processed == 1

    @pytest.mark.asyncio
    async def test_skips_invalid_price(self) -> None:
        """Skip events with unparseable price values."""
        client = _mock_client()
        engine = _ConcreteEngine(client, _make_config())
        await engine._bootstrap()

        await engine._on_price_update({"asset_id": _YES_TOKEN_ID, "price": "not_a_number"})

        assert engine._snapshots_processed == 0

    @pytest.mark.asyncio
    async def test_skips_unknown_asset(self) -> None:
        """Skip events for unregistered asset IDs."""
        client = _mock_client()
        engine = _ConcreteEngine(client, _make_config())
        await engine._bootstrap()

        await engine._on_price_update({"asset_id": "unknown", "price": "0.50"})

        assert engine._snapshots_processed == 0

    @pytest.mark.asyncio
    async def test_calls_should_skip_market_hook(self) -> None:
        """Verify _should_skip_market hook can suppress processing."""
        client = _mock_client()
        engine = _ConcreteEngine(client, _make_config())
        await engine._bootstrap()
        engine._should_skip_market = MagicMock(return_value=True)  # type: ignore[method-assign]

        await engine._on_price_update(_make_ws_event())

        assert engine._snapshots_processed == 0

    @pytest.mark.asyncio
    async def test_dispatches_signal_to_apply_signal(self) -> None:
        """Verify strategy signals are dispatched to _apply_signal."""
        client = _mock_client()
        engine = _ConcreteEngine(client, _make_config())
        await engine._bootstrap()

        test_signal = Signal(
            symbol=_CONDITION_ID,
            side=Side.BUY,
            strength=Decimal("0.1"),
            reason="test",
        )
        engine._strategy.on_snapshot.return_value = test_signal  # type: ignore[union-attr]

        await engine._on_price_update(_make_ws_event())

        assert len(engine.applied_signals) == 1

    @pytest.mark.asyncio
    async def test_mark_to_market_updates_portfolio(self) -> None:
        """Verify mark-to-market updates when position outcome tracked."""
        client = _mock_client()
        engine = _ConcreteEngine(client, _make_config())
        await engine._bootstrap()
        engine._position_outcomes[_CONDITION_ID] = "Yes"
        engine._portfolio._positions[_CONDITION_ID] = Position(
            symbol=_CONDITION_ID,
            side=Side.BUY,
            quantity=Decimal(10),
            entry_price=Decimal("0.60"),
            entry_time=1000,
        )

        await engine._on_price_update(_make_ws_event(price="0.65"))

        assert engine._portfolio._mark_prices.get(_CONDITION_ID) == Decimal("0.65")


class TestRefreshOrderBook:
    """Tests for _refresh_order_book."""

    @pytest.mark.asyncio
    async def test_refreshes_and_returns_snapshot(self) -> None:
        """Fetch fresh order book and return updated snapshot."""
        client = _mock_client()
        engine = _ConcreteEngine(client, _make_config())
        await engine._bootstrap()

        snapshot = await engine._refresh_order_book(_CONDITION_ID)

        assert snapshot is not None
        assert client.get_order_book.await_count == _MIN_TOKENS  # 1 bootstrap + 1 refresh

    @pytest.mark.asyncio
    async def test_returns_none_for_unknown_market(self) -> None:
        """Return None when market is not cached."""
        client = _mock_client()
        engine = _ConcreteEngine(client, _make_config())

        snapshot = await engine._refresh_order_book("unknown_cid")

        assert snapshot is None

    @pytest.mark.asyncio
    async def test_falls_back_on_api_error(self) -> None:
        """Return cached snapshot when refresh API call fails."""
        client = _mock_client()
        engine = _ConcreteEngine(client, _make_config())
        await engine._bootstrap()
        client.get_order_book.side_effect = PolymarketAPIError(msg="refresh failed", status_code=0)

        snapshot = await engine._refresh_order_book(_CONDITION_ID)

        # Still returns snapshot from cached data
        assert snapshot is not None


class TestRefreshOrderBooksLoop:
    """Tests for the periodic order book refresh loop."""

    @pytest.mark.asyncio
    async def test_refreshes_order_books_periodically(self) -> None:
        """Verify order books are refreshed on each loop iteration."""
        client = _mock_client()
        config = _make_config()
        engine = _ConcreteEngine(client, config)
        await engine._bootstrap()

        initial_call_count = client.get_order_book.await_count
        call_count = 0

        _original_sleep = asyncio.sleep

        async def _fast_sleep(delay: float) -> None:  # noqa: ARG001
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise asyncio.CancelledError
            await _original_sleep(0)

        with (
            pytest.raises(asyncio.CancelledError),
            patch("asyncio.sleep", side_effect=_fast_sleep),
        ):
            await engine._refresh_order_books_loop()

        assert client.get_order_book.await_count > initial_call_count

    @pytest.mark.asyncio
    async def test_skips_uncached_markets(self) -> None:
        """Skip refresh for markets not in the cache."""
        client = _mock_client()
        config = _make_config()
        engine = _ConcreteEngine(client, config)
        # Don't bootstrap — no cached markets

        call_count = 0
        _original_sleep = asyncio.sleep

        async def _fast_sleep(delay: float) -> None:  # noqa: ARG001
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise asyncio.CancelledError
            await _original_sleep(0)

        with (
            pytest.raises(asyncio.CancelledError),
            patch("asyncio.sleep", side_effect=_fast_sleep),
        ):
            await engine._refresh_order_books_loop()

        # Only the sleep call, no order book fetches
        client.get_order_book.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_continues_on_api_error(self) -> None:
        """Continue refreshing other markets when one fails."""
        client = _mock_client()
        config = _make_config()
        engine = _ConcreteEngine(client, config)
        await engine._bootstrap()
        client.get_order_book.side_effect = PolymarketAPIError(msg="refresh failed", status_code=0)

        call_count = 0
        _original_sleep = asyncio.sleep

        async def _fast_sleep(delay: float) -> None:  # noqa: ARG001
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise asyncio.CancelledError
            await _original_sleep(0)

        with (
            pytest.raises(asyncio.CancelledError),
            patch("asyncio.sleep", side_effect=_fast_sleep),
        ):
            await engine._refresh_order_books_loop()

        # Should have attempted refresh despite errors
        assert client.get_order_book.await_count > 1


class TestRotationLoop:
    """Tests for _rotation_loop."""

    @pytest.mark.asyncio
    async def test_exits_immediately_without_series_slugs(self) -> None:
        """Return immediately when no series slugs configured."""
        client = _mock_client()
        config = _make_config(series_slugs=())
        engine = _ConcreteEngine(client, config)

        # Should complete without blocking
        await asyncio.wait_for(engine._rotation_loop(), timeout=1.0)


class TestRotateMarkets:
    """Tests for _rotate_markets."""

    @pytest.mark.asyncio
    async def test_calls_rotation_close_hook(self) -> None:
        """Verify _on_rotation_close is called."""
        client = _mock_client()
        new_cid = "cond_new_market"
        new_market = _make_market(condition_id=new_cid)
        client.discover_series_markets.return_value = [(new_cid, "2026-12-31")]
        client.get_market.return_value = new_market
        config = _make_config(series_slugs=("test-series",))
        engine = _ConcreteEngine(client, config)
        engine._feed = MagicMock()
        engine._feed.update_subscription = AsyncMock()

        await engine._rotate_markets()

        assert engine.rotation_close_calls == 1

    @pytest.mark.asyncio
    async def test_discovers_and_bootstraps_new_markets(self) -> None:
        """Verify new markets are discovered and bootstrapped."""
        client = _mock_client()
        new_cid = "cond_new_rotated"
        new_market = _make_market(condition_id=new_cid)
        client.discover_series_markets.return_value = [(new_cid, "2026-12-31")]
        client.get_market.return_value = new_market
        config = _make_config(series_slugs=("test-series",))
        engine = _ConcreteEngine(client, config)
        engine._feed = MagicMock()
        engine._feed.update_subscription = AsyncMock()

        await engine._rotate_markets()

        assert new_cid in engine._cached_markets
        assert engine.performance_log_calls == 1

    @pytest.mark.asyncio
    async def test_handles_discovery_failure(self) -> None:
        """Return gracefully when discovery API fails."""
        client = _mock_client()
        client.discover_series_markets.side_effect = PolymarketAPIError(
            msg="discovery failed", status_code=0
        )
        config = _make_config(series_slugs=("test-series",))
        engine = _ConcreteEngine(client, config)

        await engine._rotate_markets()

        assert engine.rotation_close_calls == 1
        assert engine.performance_log_calls == 0  # Never reached

    @pytest.mark.asyncio
    async def test_handles_empty_discovery(self) -> None:
        """Return gracefully when no new markets discovered."""
        client = _mock_client()
        client.discover_series_markets.return_value = []
        config = _make_config(series_slugs=("test-series",))
        engine = _ConcreteEngine(client, config)

        await engine._rotate_markets()

        assert engine.rotation_close_calls == 1
        assert engine.performance_log_calls == 0

    @pytest.mark.asyncio
    async def test_handles_bootstrap_failure_during_rotation(self) -> None:
        """Continue rotation when a market fails to bootstrap."""
        client = _mock_client()
        new_cid = "cond_fail_bootstrap"
        client.discover_series_markets.return_value = [(new_cid, "2026-12-31")]
        client.get_market.side_effect = PolymarketAPIError(msg="market fetch failed", status_code=0)
        config = _make_config(series_slugs=("test-series",))
        engine = _ConcreteEngine(client, config)
        engine._feed = MagicMock()
        engine._feed.update_subscription = AsyncMock()

        await engine._rotate_markets()

        assert new_cid not in engine._cached_markets
        assert engine.performance_log_calls == 1

    @pytest.mark.asyncio
    async def test_calls_clear_market_state_hook(self) -> None:
        """Verify _clear_market_state hook is called during rotation."""
        client = _mock_client()
        new_cid = "cond_clear_hook"
        client.discover_series_markets.return_value = [(new_cid, "2026-12-31")]
        client.get_market.return_value = _make_market(condition_id=new_cid)
        config = _make_config(series_slugs=("test-series",))
        engine = _ConcreteEngine(client, config)
        engine._feed = MagicMock()
        engine._feed.update_subscription = AsyncMock()
        engine._clear_market_state = MagicMock()  # type: ignore[method-assign]

        await engine._rotate_markets()

        engine._clear_market_state.assert_called_once()


class TestHooks:
    """Tests for default hook implementations."""

    def test_on_bootstrap_market_is_noop(self) -> None:
        """Default _on_bootstrap_market does nothing."""
        engine = _ConcreteEngine(_mock_client(), _make_config())
        market = _make_market()

        # Should not raise
        engine._on_bootstrap_market(_CONDITION_ID, market)

    def test_should_skip_market_returns_false(self) -> None:
        """Default _should_skip_market returns False."""
        engine = _ConcreteEngine(_mock_client(), _make_config())

        assert engine._should_skip_market(_CONDITION_ID) is False

    def test_clear_market_state_is_noop(self) -> None:
        """Default _clear_market_state does nothing."""
        engine = _ConcreteEngine(_mock_client(), _make_config())

        # Should not raise
        engine._clear_market_state()
