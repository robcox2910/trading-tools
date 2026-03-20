"""Tests for the directional backtest runner."""

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from trading_tools.apps.directional.backtest_runner import (
    BookSnapshotCache,
    WhaleTradeCache,
    _CalibrationAccumulator,
    _empty_result,
    _metadata_to_opportunity,
    determine_outcome,
    run_directional_backtest,
    snapshot_to_order_book,
)
from trading_tools.apps.directional.config import DirectionalConfig
from trading_tools.apps.tick_collector.models import OrderBookSnapshot
from trading_tools.apps.whale_monitor.models import WhaleTrade
from trading_tools.core.models import ZERO, Candle, Interval

_BASE_TS = 1_710_000_000
_WINDOW_END = _BASE_TS + 300


def _make_candle(ts: int, open_: Decimal, close: Decimal) -> Candle:
    """Create a test candle."""
    return Candle(
        symbol="BTC-USD",
        timestamp=ts,
        open=open_,
        high=max(open_, close),
        low=min(open_, close),
        close=close,
        volume=Decimal(1000),
        interval=Interval.M1,
    )


class TestDetermineOutcome:
    """Test outcome determination from candles."""

    def test_up_when_close_above_open(self) -> None:
        """Return 'Up' when close > open."""
        candles = [_make_candle(_BASE_TS, Decimal(100), Decimal(101))]
        assert determine_outcome(candles) == "Up"

    def test_down_when_close_below_open(self) -> None:
        """Return 'Down' when close < open."""
        candles = [_make_candle(_BASE_TS, Decimal(101), Decimal(100))]
        assert determine_outcome(candles) == "Down"

    def test_none_when_flat(self) -> None:
        """Return None when close == open."""
        candles = [_make_candle(_BASE_TS, Decimal(100), Decimal(100))]
        assert determine_outcome(candles) is None

    def test_none_when_empty(self) -> None:
        """Return None when no candles."""
        assert determine_outcome([]) is None


class TestMetadataToOpportunity:
    """Test market metadata conversion."""

    def test_converts_fields(self) -> None:
        """Convert metadata fields to MarketOpportunity."""
        meta = AsyncMock()
        meta.condition_id = "cond_1"
        meta.title = "BTC Up or Down?"
        meta.asset = "BTC-USD"
        meta.up_token_id = "up_tok"
        meta.down_token_id = "down_tok"
        meta.window_start_ts = _BASE_TS
        meta.window_end_ts = _WINDOW_END

        opp = _metadata_to_opportunity(meta)
        assert opp.condition_id == "cond_1"
        assert opp.asset == "BTC-USD"
        assert opp.up_price == Decimal("0.50")
        assert opp.down_price == Decimal("0.50")


class TestCalibrationAccumulator:
    """Test the calibration metric accumulator."""

    def test_record_win(self) -> None:
        """Record a winning result updates wins and pnl."""
        acc = _CalibrationAccumulator()
        result = AsyncMock()
        result.pnl = Decimal("5.00")
        result.p_up = Decimal("0.65")
        result.predicted_side = "Up"
        result.winning_side = "Up"
        acc.record(result)
        assert acc.wins == 1
        assert acc.losses == 0
        assert acc.total_pnl == Decimal("5.00")
        assert acc.brier_count == 1

    def test_record_loss(self) -> None:
        """Record a losing result updates losses and pnl."""
        acc = _CalibrationAccumulator()
        result = AsyncMock()
        result.pnl = Decimal("-3.00")
        result.p_up = Decimal("0.65")
        result.predicted_side = "Up"
        result.winning_side = "Down"
        acc.record(result)
        assert acc.wins == 0
        assert acc.losses == 1
        assert acc.total_pnl == Decimal("-3.00")


class TestEmptyResult:
    """Test empty result factory."""

    def test_returns_zero_metrics(self) -> None:
        """Empty result has zero metrics."""
        result = _empty_result(Decimal(1000))
        assert result.initial_capital == Decimal(1000)
        assert result.final_capital == Decimal(1000)
        assert result.total_pnl == ZERO
        assert result.total_trades == 0
        assert result.brier_score == ZERO


class TestSnapshotToOrderBook:
    """Test order book snapshot conversion."""

    def test_converts_json_to_order_levels(self) -> None:
        """Parse bids/asks JSON arrays into OrderLevel tuples."""
        snapshot = OrderBookSnapshot()
        snapshot.token_id = "tok_1"
        snapshot.timestamp = 1_710_000_000_000
        snapshot.bids_json = "[[0.48, 100], [0.47, 200]]"
        snapshot.asks_json = "[[0.52, 150], [0.53, 250]]"
        snapshot.spread = 0.04
        snapshot.midpoint = 0.50

        book = snapshot_to_order_book(snapshot)
        assert book.token_id == "tok_1"
        assert len(book.bids) == 2
        assert len(book.asks) == 2
        assert book.bids[0].price == Decimal("0.48")
        assert book.bids[0].size == Decimal(100)
        assert book.asks[0].price == Decimal("0.52")
        assert book.spread == Decimal("0.04")
        assert book.midpoint == Decimal("0.5")

    def test_handles_empty_levels(self) -> None:
        """Handle empty bid/ask arrays."""
        snapshot = OrderBookSnapshot()
        snapshot.token_id = "tok_2"
        snapshot.timestamp = 1_710_000_000_000
        snapshot.bids_json = "[]"
        snapshot.asks_json = "[]"
        snapshot.spread = 0.0
        snapshot.midpoint = 0.50

        book = snapshot_to_order_book(snapshot)
        assert len(book.bids) == 0
        assert len(book.asks) == 0


class TestRunDirectionalBacktest:
    """Test the full backtest runner with mocked repository."""

    @pytest.mark.asyncio
    async def test_no_metadata_returns_empty(self) -> None:
        """Return empty result when no market metadata found."""
        config = DirectionalConfig()
        repo = AsyncMock()
        repo.get_market_metadata_in_range = AsyncMock(return_value=[])

        result = await run_directional_backtest(
            config=config,
            repo=repo,
            start_ts=_BASE_TS,
            end_ts=_WINDOW_END,
        )
        assert result.total_windows == 0
        assert result.total_pnl == ZERO

    @pytest.mark.asyncio
    async def test_skips_windows_without_candles(self) -> None:
        """Skip windows when no candles are available (no outcome)."""
        config = DirectionalConfig()
        meta = AsyncMock()
        meta.condition_id = "cond_1"
        meta.title = "BTC Up or Down?"
        meta.asset = "BTC-USD"
        meta.up_token_id = "up_tok"
        meta.down_token_id = "down_tok"
        meta.window_start_ts = _BASE_TS
        meta.window_end_ts = _WINDOW_END

        repo = AsyncMock()
        repo.get_market_metadata_in_range = AsyncMock(return_value=[meta])
        repo.get_nearest_book_snapshot = AsyncMock(return_value=None)

        result = await run_directional_backtest(
            config=config,
            repo=repo,
            start_ts=_BASE_TS,
            end_ts=_WINDOW_END,
            candles_by_asset={},
        )
        assert result.total_windows == 1
        assert result.skipped == 1
        assert result.total_trades == 0

    @pytest.mark.asyncio
    async def test_loads_whale_signal_when_repo_provided(self) -> None:
        """Pass whale signal to replay adapter when whale_repo is given."""
        config = DirectionalConfig()
        meta = AsyncMock()
        meta.condition_id = "cond_1"
        meta.title = "BTC Up or Down?"
        meta.asset = "BTC-USD"
        meta.up_token_id = "up_tok"
        meta.down_token_id = "down_tok"
        meta.window_start_ts = _BASE_TS
        meta.window_end_ts = _WINDOW_END

        repo = AsyncMock()
        repo.get_market_metadata_in_range = AsyncMock(return_value=[meta])
        repo.get_nearest_book_snapshot = AsyncMock(return_value=None)

        whale_repo = AsyncMock()
        whale_repo.get_whale_signal = AsyncMock(return_value=0.8)

        # Provide candles so the window doesn't skip due to no outcome
        candles = [_make_candle(_BASE_TS, Decimal(100), Decimal(101))]

        result = await run_directional_backtest(
            config=config,
            repo=repo,
            start_ts=_BASE_TS,
            end_ts=_WINDOW_END,
            candles_by_asset={"BTC-USD": candles},
            whale_repo=whale_repo,
        )
        entry_eval_ts = _WINDOW_END - config.entry_window_start
        whale_repo.get_whale_signal.assert_called_once_with("cond_1", before_ts=entry_eval_ts)
        # Window was processed (not skipped due to missing candles)
        assert result.total_windows == 1

    @pytest.mark.asyncio
    async def test_uses_snapshot_cache_when_provided(self) -> None:
        """Use snapshot cache instead of DB queries when provided."""
        config = DirectionalConfig()
        meta = AsyncMock()
        meta.condition_id = "cond_1"
        meta.title = "BTC Up or Down?"
        meta.asset = "BTC-USD"
        meta.up_token_id = "up_tok"
        meta.down_token_id = "down_tok"
        meta.window_start_ts = _BASE_TS
        meta.window_end_ts = _WINDOW_END

        repo = AsyncMock()
        repo.get_market_metadata_in_range = AsyncMock(return_value=[meta])

        candles = [_make_candle(_BASE_TS, Decimal(100), Decimal(101))]
        snapshot_cache = BookSnapshotCache([])

        result = await run_directional_backtest(
            config=config,
            repo=repo,
            start_ts=_BASE_TS,
            end_ts=_WINDOW_END,
            candles_by_asset={"BTC-USD": candles},
            snapshot_cache=snapshot_cache,
        )
        # Should not call DB for snapshots when cache is provided
        repo.get_nearest_book_snapshot.assert_not_called()
        assert result.total_windows == 1

    @pytest.mark.asyncio
    async def test_uses_whale_cache_when_provided(self) -> None:
        """Use whale cache instead of DB queries when provided."""
        config = DirectionalConfig()
        meta = AsyncMock()
        meta.condition_id = "cond_1"
        meta.title = "BTC Up or Down?"
        meta.asset = "BTC-USD"
        meta.up_token_id = "up_tok"
        meta.down_token_id = "down_tok"
        meta.window_start_ts = _BASE_TS
        meta.window_end_ts = _WINDOW_END

        repo = AsyncMock()
        repo.get_market_metadata_in_range = AsyncMock(return_value=[meta])
        repo.get_nearest_book_snapshot = AsyncMock(return_value=None)

        candles = [_make_candle(_BASE_TS, Decimal(100), Decimal(101))]

        trade = WhaleTrade(
            whale_address="0xaaa",
            transaction_hash="tx_1",
            side="BUY",
            asset_id="asset_1",
            condition_id="cond_1",
            size=100.0,
            price=0.60,
            timestamp=_BASE_TS,
            title="Test",
            slug="test",
            outcome="Up",
            outcome_index=0,
            collected_at=_BASE_TS * 1000,
        )
        whale_cache = WhaleTradeCache([trade])

        whale_repo = AsyncMock()

        result = await run_directional_backtest(
            config=config,
            repo=repo,
            start_ts=_BASE_TS,
            end_ts=_WINDOW_END,
            candles_by_asset={"BTC-USD": candles},
            whale_repo=whale_repo,
            whale_cache=whale_cache,
        )
        # Should not call DB for whale signal when cache is provided
        whale_repo.get_whale_signal.assert_not_called()
        assert result.total_windows == 1

    @pytest.mark.asyncio
    async def test_uses_pre_loaded_metadata(self) -> None:
        """Skip metadata DB query when metadata_list is provided."""
        config = DirectionalConfig()
        meta = AsyncMock()
        meta.condition_id = "cond_1"
        meta.title = "BTC Up or Down?"
        meta.asset = "BTC-USD"
        meta.up_token_id = "up_tok"
        meta.down_token_id = "down_tok"
        meta.window_start_ts = _BASE_TS
        meta.window_end_ts = _WINDOW_END

        repo = AsyncMock()
        repo.get_nearest_book_snapshot = AsyncMock(return_value=None)

        candles = [_make_candle(_BASE_TS, Decimal(100), Decimal(101))]

        result = await run_directional_backtest(
            config=config,
            repo=repo,
            start_ts=_BASE_TS,
            end_ts=_WINDOW_END,
            candles_by_asset={"BTC-USD": candles},
            metadata_list=[meta],
        )
        # Should not query metadata from DB
        repo.get_market_metadata_in_range.assert_not_called()
        assert result.total_windows == 1


_COLLECTED_AT = 1_710_000_000_000


def _make_whale_trade(
    condition_id: str = "cond_1",
    outcome: str = "Up",
    size: float = 100.0,
    price: float = 0.60,
    timestamp: int = _BASE_TS,
    tx_hash: str = "tx_1",
) -> WhaleTrade:
    """Create a WhaleTrade for testing.

    Args:
        condition_id: Market condition ID.
        outcome: Outcome label.
        size: Token quantity.
        price: Execution price.
        timestamp: Epoch seconds.
        tx_hash: Unique transaction hash.

    Returns:
        A new WhaleTrade instance.

    """
    return WhaleTrade(
        whale_address="0xaaa",
        transaction_hash=tx_hash,
        side="BUY",
        asset_id="asset_1",
        condition_id=condition_id,
        size=size,
        price=price,
        timestamp=timestamp,
        title="Test",
        slug="test",
        outcome=outcome,
        outcome_index=0,
        collected_at=_COLLECTED_AT,
    )


class TestBookSnapshotCache:
    """Test the in-memory order book snapshot cache."""

    def _make_snapshot(
        self,
        token_id: str = "tok_a",
        timestamp: int = _BASE_TS * 1000,
    ) -> OrderBookSnapshot:
        """Create a snapshot for testing.

        Args:
            token_id: CLOB token ID.
            timestamp: Epoch milliseconds.

        Returns:
            A new OrderBookSnapshot instance.

        """
        snap = OrderBookSnapshot()
        snap.token_id = token_id
        snap.timestamp = timestamp
        snap.bids_json = "[[0.48, 100]]"
        snap.asks_json = "[[0.52, 150]]"
        snap.spread = 0.04
        snap.midpoint = 0.50
        return snap

    def test_get_nearest_exact_match(self) -> None:
        """Return exact match when timestamp matches perfectly."""
        snap = self._make_snapshot(timestamp=5000)
        cache = BookSnapshotCache([snap])
        result = cache.get_nearest("tok_a", 5000)
        assert result is snap

    def test_get_nearest_within_tolerance(self) -> None:
        """Return closest snapshot within the tolerance window."""
        snap1 = self._make_snapshot(timestamp=1000)
        snap2 = self._make_snapshot(timestamp=4000)
        cache = BookSnapshotCache([snap1, snap2])
        result = cache.get_nearest("tok_a", 3500, tolerance_ms=1000)
        assert result is snap2

    def test_get_nearest_none_outside_tolerance(self) -> None:
        """Return None when no snapshot exists within tolerance."""
        snap = self._make_snapshot(timestamp=1000)
        cache = BookSnapshotCache([snap])
        result = cache.get_nearest("tok_a", 50000, tolerance_ms=5000)
        assert result is None

    def test_get_nearest_unknown_token(self) -> None:
        """Return None for a token with no snapshots."""
        cache = BookSnapshotCache([])
        result = cache.get_nearest("unknown", 5000)
        assert result is None

    def test_get_nearest_picks_closer_of_two(self) -> None:
        """Pick the closer of two candidates straddling the target."""
        snap1 = self._make_snapshot(timestamp=1000)
        snap2 = self._make_snapshot(timestamp=3000)
        cache = BookSnapshotCache([snap1, snap2])
        # 2100 is closer to 3000 (dist=900) than 1000 (dist=1100)
        result = cache.get_nearest("tok_a", 2100, tolerance_ms=2000)
        assert result is snap2

    def test_multiple_tokens_isolated(self) -> None:
        """Snapshots from different tokens don't interfere."""
        snap_a = self._make_snapshot(token_id="tok_a", timestamp=1000)
        snap_b = self._make_snapshot(token_id="tok_b", timestamp=2000)
        cache = BookSnapshotCache([snap_a, snap_b])
        assert cache.get_nearest("tok_a", 1000) is snap_a
        assert cache.get_nearest("tok_b", 2000) is snap_b
        assert cache.get_nearest("tok_a", 2000, tolerance_ms=500) is None


class TestWhaleTradeCache:
    """Test the in-memory whale trade cache."""

    def test_get_signal_returns_continuous_ratio(self) -> None:
        """Return continuous (up_vol - down_vol) / total_vol ratio."""
        trades = [
            _make_whale_trade(outcome="Up", size=100, price=0.60, tx_hash="tx_1"),
            _make_whale_trade(outcome="Down", size=30, price=0.40, tx_hash="tx_2"),
        ]
        cache = WhaleTradeCache(trades)
        signal = cache.get_signal("cond_1")
        assert signal is not None
        # Up vol = 60, Down vol = 12, ratio = (60-12)/72 ≈ 0.667
        assert signal > 0
        assert signal < 1.0

    def test_get_signal_none_when_no_trades(self) -> None:
        """Return None when no trades exist for the condition."""
        cache = WhaleTradeCache([])
        assert cache.get_signal("cond_1") is None

    def test_get_signal_respects_before_ts(self) -> None:
        """Exclude trades after the before_ts cutoff."""
        trades = [
            _make_whale_trade(
                outcome="Up", size=100, price=0.60, timestamp=_BASE_TS, tx_hash="tx_1"
            ),
            _make_whale_trade(
                outcome="Down", size=200, price=0.60, timestamp=_BASE_TS + 100, tx_hash="tx_2"
            ),
        ]
        cache = WhaleTradeCache(trades)
        # With cutoff before the Down trade, only Up is counted → ratio = 1.0
        signal_before = cache.get_signal("cond_1", before_ts=_BASE_TS + 50)
        assert signal_before is not None
        assert signal_before > 0
        # Without cutoff, Down wins → ratio < 0
        signal_all = cache.get_signal("cond_1")
        assert signal_all is not None
        assert signal_all < 0

    def test_get_signal_ignores_non_directional_outcomes(self) -> None:
        """Return None when the only outcome is not Up or Down."""
        trade = _make_whale_trade(outcome="Yes", tx_hash="tx_1")
        cache = WhaleTradeCache([trade])
        # "Yes" volume but no Up/Down → total of Up+Down = 0 → None
        assert cache.get_signal("cond_1") is None

    def test_multiple_conditions_isolated(self) -> None:
        """Trades from different conditions don't interfere."""
        t1 = _make_whale_trade(condition_id="c1", outcome="Up", tx_hash="tx_1")
        t2 = _make_whale_trade(condition_id="c2", outcome="Down", tx_hash="tx_2")
        cache = WhaleTradeCache([t1, t2])
        c1_signal = cache.get_signal("c1")
        c2_signal = cache.get_signal("c2")
        assert c1_signal is not None
        assert c1_signal > 0  # Up dominant → positive
        assert c2_signal is not None
        assert c2_signal < 0  # Down dominant → negative
