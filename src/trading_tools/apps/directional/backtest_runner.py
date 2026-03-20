"""Replay engine for backtesting the directional trading algorithm.

Load historical market metadata, order book snapshots, and Binance
candles from the database.  For each market window, extract features,
estimate P(Up), apply Kelly sizing, and compute directional P&L.
Track calibration via Brier score alongside standard win/loss metrics.

Provides ``BookSnapshotCache`` and ``WhaleTradeCache`` for grid search
pre-fetching, which eliminates per-window DB round-trips when the same
data is reused across many parameter combinations.
"""

from __future__ import annotations

import bisect
import dataclasses
import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from trading_tools.clients.polymarket.models import OrderBook, OrderLevel
from trading_tools.core.models import ONE, ZERO

from .adapters import BacktestExecution, ReplayMarketData
from .engine import DirectionalEngine
from .estimator import ProbabilityEstimator
from .models import MarketOpportunity

if TYPE_CHECKING:
    from collections.abc import Sequence

    from trading_tools.apps.tick_collector.models import MarketMetadata, OrderBookSnapshot
    from trading_tools.apps.tick_collector.repository import TickRepository
    from trading_tools.apps.whale_monitor.models import WhaleTrade
    from trading_tools.apps.whale_monitor.repository import WhaleRepository
    from trading_tools.core.models import Candle

    from .config import DirectionalConfig
    from .models import DirectionalResult

logger = logging.getLogger(__name__)

_MS_PER_SECOND = 1000
_HUNDRED = Decimal(100)
_HALF = Decimal("0.50")


class BookSnapshotCache:
    """In-memory cache of order book snapshots keyed by token ID.

    Build from a bulk snapshot list and provide O(log n) nearest-match
    lookups via bisect.  Eliminates per-window DB queries during grid
    search backtests where the same snapshot data is reused across many
    parameter combinations.
    """

    def __init__(self, snapshots: Sequence[OrderBookSnapshot]) -> None:
        """Build the cache from a pre-sorted snapshot list.

        Args:
            snapshots: Order book snapshots sorted by
                ``(token_id, timestamp)``.

        """
        self._by_token: dict[str, list[OrderBookSnapshot]] = defaultdict(list)
        self._ts_by_token: dict[str, list[int]] = defaultdict(list)
        for snap in snapshots:
            self._by_token[snap.token_id].append(snap)
            self._ts_by_token[snap.token_id].append(snap.timestamp)

    def get_nearest(
        self,
        token_id: str,
        timestamp_ms: int,
        tolerance_ms: int = 5000,
    ) -> OrderBookSnapshot | None:
        """Find the snapshot nearest to a given timestamp.

        Use binary search to locate the closest match within the
        tolerance window.

        Args:
            token_id: CLOB token identifier.
            timestamp_ms: Target epoch milliseconds.
            tolerance_ms: Maximum allowed distance in milliseconds.

        Returns:
            The nearest ``OrderBookSnapshot``, or ``None`` if nothing
            exists within the tolerance window.

        """
        timestamps = self._ts_by_token.get(token_id)
        if not timestamps:
            return None

        snaps = self._by_token[token_id]
        idx = bisect.bisect_left(timestamps, timestamp_ms)

        best: OrderBookSnapshot | None = None
        best_dist = tolerance_ms + 1

        for candidate_idx in (idx - 1, idx):
            if 0 <= candidate_idx < len(timestamps):
                dist = abs(timestamps[candidate_idx] - timestamp_ms)
                if dist <= tolerance_ms and dist < best_dist:
                    best = snaps[candidate_idx]
                    best_dist = dist

        return best


class WhaleTradeCache:
    """In-memory cache of whale BUY trades keyed by condition ID.

    Build from a bulk trade list and provide directional signal lookups
    without DB queries.  Eliminates per-window whale signal fetches
    during grid search backtests.
    """

    def __init__(self, trades: Sequence[WhaleTrade]) -> None:
        """Build the cache from a pre-sorted trade list.

        Args:
            trades: BUY-side whale trades sorted by
                ``(condition_id, timestamp)``.

        """
        self._by_condition: dict[str, list[WhaleTrade]] = defaultdict(list)
        for trade in trades:
            self._by_condition[trade.condition_id].append(trade)

    def get_signal(self, condition_id: str, *, before_ts: int | None = None) -> str | None:
        """Return the whale directional bet for a market.

        Filter BUY trades by timestamp, group by outcome, and return
        the outcome with the larger total dollar volume (size * price).

        Args:
            condition_id: Polymarket market condition identifier.
            before_ts: Only consider trades at or before this epoch-second
                timestamp.  Use in backtesting to prevent look-ahead bias.

        Returns:
            ``"Up"`` or ``"Down"`` if a whale has a clear directional
            bet, ``None`` if no whale activity.

        """
        trades = self._by_condition.get(condition_id)
        if not trades:
            return None

        volume_by_outcome: dict[str, float] = defaultdict(float)
        for trade in trades:
            if before_ts is not None and trade.timestamp > before_ts:
                continue
            volume_by_outcome[trade.outcome] += trade.size * trade.price

        if not volume_by_outcome:
            return None

        top_outcome = max(volume_by_outcome, key=lambda k: volume_by_outcome[k])
        if top_outcome in ("Up", "Down"):
            return top_outcome
        return None


@dataclass(frozen=True)
class DirectionalBacktestResult:
    """Aggregate result from a directional backtest run.

    Attributes:
        initial_capital: Starting virtual capital.
        final_capital: Ending capital after all settlements.
        total_pnl: Net P&L across all windows.
        return_pct: Return as a percentage of initial capital.
        total_windows: Number of market windows replayed.
        total_trades: Number of positions entered.
        wins: Positions with positive P&L.
        losses: Positions with negative P&L.
        skipped: Windows skipped (insufficient data or no edge).
        win_rate: Fraction of profitable positions (0-1).
        avg_pnl: Average P&L per settled position.
        brier_score: Mean squared error of probability predictions.
        avg_p_when_correct: Average predicted probability when correct.
        avg_p_when_incorrect: Average predicted probability when incorrect.

    """

    initial_capital: Decimal
    final_capital: Decimal
    total_pnl: Decimal
    return_pct: Decimal
    total_windows: int
    total_trades: int
    wins: int
    losses: int
    skipped: int
    win_rate: Decimal
    avg_pnl: Decimal
    brier_score: Decimal
    avg_p_when_correct: Decimal
    avg_p_when_incorrect: Decimal


@dataclass
class _CalibrationAccumulator:
    """Track calibration metrics across multiple results."""

    brier_sum: Decimal = ZERO
    brier_count: int = 0
    p_correct_sum: Decimal = ZERO
    p_correct_count: int = 0
    p_incorrect_sum: Decimal = ZERO
    p_incorrect_count: int = 0
    total_pnl: Decimal = ZERO
    wins: int = 0
    losses: int = 0
    skipped: int = 0
    total_trades: int = 0

    def record(self, result: DirectionalResult) -> None:
        """Record a single result's metrics.

        Args:
            result: A settled directional result.

        """
        self.total_pnl += result.pnl
        self.total_trades += 1
        if result.pnl > ZERO:
            self.wins += 1
        elif result.pnl < ZERO:
            self.losses += 1

        actual_up = Decimal(1) if result.winning_side == "Up" else ZERO
        self.brier_sum += (result.p_up - actual_up) ** 2
        self.brier_count += 1

        p_win = result.p_up if result.predicted_side == "Up" else ONE - result.p_up
        if result.predicted_side == result.winning_side:
            self.p_correct_sum += p_win
            self.p_correct_count += 1
        else:
            self.p_incorrect_sum += p_win
            self.p_incorrect_count += 1


def _metadata_to_opportunity(meta: MarketMetadata) -> MarketOpportunity:
    """Convert a ``MarketMetadata`` record to a ``MarketOpportunity``.

    Initial prices are set to 0.50/0.50 since actual prices come from
    order book snapshots during replay.

    Args:
        meta: Market metadata from the database.

    Returns:
        A ``MarketOpportunity`` with metadata fields populated.

    """
    return MarketOpportunity(
        condition_id=meta.condition_id,
        title=meta.title,
        asset=meta.asset,
        up_token_id=meta.up_token_id,
        down_token_id=meta.down_token_id,
        window_start_ts=meta.window_start_ts,
        window_end_ts=meta.window_end_ts,
        up_price=_HALF,
        down_price=_HALF,
        series_slug=meta.series_slug,
    )


def determine_outcome(candles: Sequence[Candle]) -> str | None:
    """Determine the winning side from candle data.

    Args:
        candles: 1-min candles spanning the market window.

    Returns:
        ``"Up"`` if close > open, ``"Down"`` if close < open, ``None`` if flat.

    """
    if not candles:
        return None
    open_price = candles[0].open
    close_price = candles[-1].close
    if close_price > open_price:
        return "Up"
    if close_price < open_price:
        return "Down"
    return None


def make_default_book(token_id: str) -> OrderBook:
    """Create a default order book with symmetric 0.50 prices.

    Args:
        token_id: CLOB token identifier.

    Returns:
        An ``OrderBook`` with default bid/ask at 0.50.

    """
    return OrderBook(
        token_id=token_id,
        bids=(OrderLevel(price=_HALF, size=Decimal(100)),),
        asks=(OrderLevel(price=_HALF, size=Decimal(100)),),
        spread=Decimal("0.01"),
        midpoint=_HALF,
        min_order_size=Decimal(5),
    )


def snapshot_to_order_book(snapshot: OrderBookSnapshot) -> OrderBook:
    """Convert a database ``OrderBookSnapshot`` to an ``OrderBook``.

    Parse the JSON bid/ask arrays into typed ``OrderLevel`` tuples.

    Args:
        snapshot: Order book snapshot from the tick collector database.

    Returns:
        An ``OrderBook`` with parsed levels, spread, and midpoint.

    """
    bids_raw: list[list[float]] = json.loads(snapshot.bids_json)
    asks_raw: list[list[float]] = json.loads(snapshot.asks_json)

    bids = tuple(OrderLevel(price=Decimal(str(b[0])), size=Decimal(str(b[1]))) for b in bids_raw)
    asks = tuple(OrderLevel(price=Decimal(str(a[0])), size=Decimal(str(a[1]))) for a in asks_raw)

    return OrderBook(
        token_id=snapshot.token_id,
        bids=bids,
        asks=asks,
        spread=Decimal(str(snapshot.spread)),
        midpoint=Decimal(str(snapshot.midpoint)),
    )


async def run_directional_backtest(
    config: DirectionalConfig,
    repo: TickRepository,
    start_ts: int,
    end_ts: int,
    *,
    candles_by_asset: dict[str, list[Candle]] | None = None,
    series_slug: str | None = None,
    whale_repo: WhaleRepository | None = None,
    metadata_list: list[MarketMetadata] | None = None,
    snapshot_cache: BookSnapshotCache | None = None,
    whale_cache: WhaleTradeCache | None = None,
) -> DirectionalBacktestResult:
    """Run a directional backtest over a date range.

    Load market metadata from the tick database, then replay each market
    window through the ``DirectionalEngine``.  For each window, step the
    clock from window start through the entry window to settlement.

    When ``snapshot_cache`` and ``whale_cache`` are provided (e.g. from
    a grid search), use them instead of per-window DB queries for order
    book snapshots and whale signals.

    Args:
        config: Algorithm configuration for the backtest.
        repo: Tick repository for loading historical data.
        start_ts: Start epoch seconds (inclusive).
        end_ts: End epoch seconds (inclusive).
        candles_by_asset: Pre-loaded Binance candles keyed by asset.
            When ``None``, no candles are available and windows are skipped.
        series_slug: Filter metadata to a specific series slug.
        whale_repo: Whale trade repository for directional signals.
            When ``None``, whale signals are omitted (weight is dead).
        metadata_list: Pre-loaded market metadata.  When provided, skip
            the metadata DB query (used by grid search to avoid
            redundant fetches).
        snapshot_cache: Pre-built order book snapshot cache.  When
            provided, use in-memory lookups instead of per-window DB
            queries.
        whale_cache: Pre-built whale trade cache.  When provided, use
            in-memory lookups instead of per-window DB queries.

    Returns:
        Aggregate backtest result with performance and calibration metrics.

    """
    if metadata_list is None:
        metadata_list = await repo.get_market_metadata_in_range(
            start_ts, end_ts, series_slug=series_slug
        )

    if not metadata_list:
        logger.warning("No market metadata found for %d - %d", start_ts, end_ts)
        return _empty_result(config.capital)

    logger.info("Loaded %d market windows for directional backtest", len(metadata_list))

    candles = candles_by_asset or {}
    acc = _CalibrationAccumulator()

    for meta in metadata_list:
        await _replay_window(
            config,
            repo,
            meta,
            candles,
            acc,
            whale_repo=whale_repo,
            snapshot_cache=snapshot_cache,
            whale_cache=whale_cache,
        )

    return _build_result(config.capital, len(metadata_list), acc)


async def _replay_window(
    config: DirectionalConfig,
    repo: TickRepository,
    meta: MarketMetadata,
    candles: dict[str, list[Candle]],
    acc: _CalibrationAccumulator,
    *,
    whale_repo: WhaleRepository | None = None,
    snapshot_cache: BookSnapshotCache | None = None,
    whale_cache: WhaleTradeCache | None = None,
) -> None:
    """Replay a single market window through the engine.

    Load real order book snapshots and whale signals from the database
    when available, falling back to defaults when data is missing.
    When caches are provided, use in-memory lookups instead of DB
    queries.

    Args:
        config: Algorithm configuration.
        repo: Tick repository for order book snapshots.
        meta: Market metadata for this window.
        candles: Pre-loaded candles keyed by asset.
        acc: Accumulator for metrics.
        whale_repo: Whale repository for directional signals.
        snapshot_cache: Pre-built snapshot cache (grid search mode).
        whale_cache: Pre-built whale trade cache (grid search mode).

    """
    opp = _metadata_to_opportunity(meta)

    asset_candles = candles.get(meta.asset, [])
    window_candles = [
        c for c in asset_candles if meta.window_start_ts <= c.timestamp <= meta.window_end_ts
    ]
    outcome = determine_outcome(window_candles)

    if outcome is None:
        acc.skipped += 1
        return

    # Load real order book snapshots near entry evaluation time
    entry_eval_ts = meta.window_end_ts - config.entry_window_start
    entry_eval_ms = entry_eval_ts * _MS_PER_SECOND

    if snapshot_cache is not None:
        up_snapshot = snapshot_cache.get_nearest(
            meta.up_token_id, entry_eval_ms, tolerance_ms=30_000
        )
        down_snapshot = snapshot_cache.get_nearest(
            meta.down_token_id, entry_eval_ms, tolerance_ms=30_000
        )
    else:
        up_snapshot = await repo.get_nearest_book_snapshot(
            meta.up_token_id, entry_eval_ms, tolerance_ms=30_000
        )
        down_snapshot = await repo.get_nearest_book_snapshot(
            meta.down_token_id, entry_eval_ms, tolerance_ms=30_000
        )

    up_book = (
        snapshot_to_order_book(up_snapshot) if up_snapshot else make_default_book(meta.up_token_id)
    )
    down_book = (
        snapshot_to_order_book(down_snapshot)
        if down_snapshot
        else make_default_book(meta.down_token_id)
    )

    # Update market prices from real order book data
    if up_snapshot or down_snapshot:
        up_price = up_book.asks[0].price if up_book.asks else up_book.midpoint
        down_price = down_book.asks[0].price if down_book.asks else down_book.midpoint
        opp = dataclasses.replace(opp, up_price=up_price, down_price=down_price)

    replay_md = ReplayMarketData()
    replay_md.set_markets([opp])
    replay_md.set_candles(meta.asset, asset_candles)
    replay_md.set_outcome(meta.condition_id, outcome)
    replay_md.set_order_books(meta.condition_id, up_book, down_book)

    # Load whale signal — prefer cache, fall back to DB query
    signal: str | None = None
    if whale_cache is not None:
        signal = whale_cache.get_signal(meta.condition_id, before_ts=entry_eval_ts)
    elif whale_repo is not None:
        signal = await whale_repo.get_whale_signal(meta.condition_id, before_ts=entry_eval_ts)
    if signal is not None:
        replay_md.set_whale_signal(meta.condition_id, signal)

    # Register BTC candles for non-BTC assets (leader momentum feature)
    if meta.asset != "BTC-USD" and "BTC-USD" in candles:
        replay_md.set_candles("BTC-USD", candles["BTC-USD"])

    execution = BacktestExecution(capital=config.capital)
    estimator = ProbabilityEstimator.for_slug(config, meta.series_slug)

    engine = DirectionalEngine(
        config=config,
        execution=execution,
        market_data=replay_md,
        estimator=estimator,
        mode_label="BACKTEST",
    )

    # Skip to entry window opening — the engine does no meaningful work
    # before entries become eligible (pure time gate, no accumulated state).
    # This cuts ~90% of poll cycles in backtesting.
    settle_ts = meta.window_end_ts + 1
    entry_open_ts = meta.window_end_ts - config.entry_window_start
    t = max(entry_open_ts, meta.window_start_ts)
    while t <= settle_ts:
        await engine.poll_cycle(t)
        t += config.poll_interval

    # If the loop stepped past settle_ts without landing on it, fire it once
    if (t - config.poll_interval) != settle_ts:
        await engine.poll_cycle(settle_ts)

    for result in engine.results:
        acc.record(result)

    if not engine.results:
        acc.skipped += 1

    logger.info(
        "WINDOW %s %s→%s outcome=%s trades=%d pnl=%.4f",
        meta.asset,
        meta.window_start_ts,
        meta.window_end_ts,
        outcome,
        len(engine.results),
        sum(r.pnl for r in engine.results),
    )


def _build_result(
    capital: Decimal, total_windows: int, acc: _CalibrationAccumulator
) -> DirectionalBacktestResult:
    """Build the final backtest result from accumulated metrics.

    Args:
        capital: Initial capital.
        total_windows: Total number of windows replayed.
        acc: Accumulated metrics.

    Returns:
        A ``DirectionalBacktestResult`` with all computed metrics.

    """
    final_capital = capital + acc.total_pnl
    return_pct = (acc.total_pnl / capital * _HUNDRED) if capital > ZERO else ZERO
    settled = acc.wins + acc.losses
    win_rate = Decimal(acc.wins) / Decimal(settled) if settled > 0 else ZERO
    avg_pnl = acc.total_pnl / Decimal(acc.total_trades) if acc.total_trades > 0 else ZERO
    brier = acc.brier_sum / Decimal(acc.brier_count) if acc.brier_count > 0 else ZERO
    avg_p_c = acc.p_correct_sum / Decimal(acc.p_correct_count) if acc.p_correct_count > 0 else ZERO
    avg_p_i = (
        acc.p_incorrect_sum / Decimal(acc.p_incorrect_count) if acc.p_incorrect_count > 0 else ZERO
    )

    return DirectionalBacktestResult(
        initial_capital=capital,
        final_capital=final_capital,
        total_pnl=acc.total_pnl,
        return_pct=return_pct,
        total_windows=total_windows,
        total_trades=acc.total_trades,
        wins=acc.wins,
        losses=acc.losses,
        skipped=acc.skipped,
        win_rate=win_rate,
        avg_pnl=avg_pnl,
        brier_score=brier,
        avg_p_when_correct=avg_p_c,
        avg_p_when_incorrect=avg_p_i,
    )


def _empty_result(capital: Decimal) -> DirectionalBacktestResult:
    """Return an empty backtest result when no data is available.

    Args:
        capital: Initial capital amount.

    Returns:
        A ``DirectionalBacktestResult`` with all metrics at zero.

    """
    return DirectionalBacktestResult(
        initial_capital=capital,
        final_capital=capital,
        total_pnl=ZERO,
        return_pct=ZERO,
        total_windows=0,
        total_trades=0,
        wins=0,
        losses=0,
        skipped=0,
        win_rate=ZERO,
        avg_pnl=ZERO,
        brier_score=ZERO,
        avg_p_when_correct=ZERO,
        avg_p_when_incorrect=ZERO,
    )
