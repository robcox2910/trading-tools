"""Replay engine for backtesting the directional trading algorithm.

Load historical market metadata, order book snapshots, and Binance
candles from the database.  For each market window, extract features,
estimate P(Up), apply Kelly sizing, and compute directional P&L.
Track calibration via Brier score alongside standard win/loss metrics.
"""

from __future__ import annotations

import logging
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

    from trading_tools.apps.tick_collector.models import MarketMetadata
    from trading_tools.apps.tick_collector.repository import TickRepository
    from trading_tools.core.models import Candle

    from .config import DirectionalConfig
    from .models import DirectionalResult

logger = logging.getLogger(__name__)

_MS_PER_SECOND = 1000
_HUNDRED = Decimal(100)
_HALF = Decimal("0.50")


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
    )


def _determine_outcome(candles: Sequence[Candle]) -> str | None:
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


def _make_default_book(token_id: str) -> OrderBook:
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


async def run_directional_backtest(
    config: DirectionalConfig,
    repo: TickRepository,
    start_ts: int,
    end_ts: int,
    *,
    candles_by_asset: dict[str, list[Candle]] | None = None,
    series_slug: str | None = None,
) -> DirectionalBacktestResult:
    """Run a directional backtest over a date range.

    Load market metadata from the tick database, then replay each market
    window through the ``DirectionalEngine``.  For each window, step the
    clock from window start through the entry window to settlement.

    Args:
        config: Algorithm configuration for the backtest.
        repo: Tick repository for loading historical data.
        start_ts: Start epoch seconds (inclusive).
        end_ts: End epoch seconds (inclusive).
        candles_by_asset: Pre-loaded Binance candles keyed by asset.
            When ``None``, no candles are available and windows are skipped.
        series_slug: Filter metadata to a specific series slug.

    Returns:
        Aggregate backtest result with performance and calibration metrics.

    """
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
        await _replay_window(config, repo, meta, candles, acc)

    return _build_result(config.capital, len(metadata_list), acc)


async def _replay_window(
    config: DirectionalConfig,
    _repo: TickRepository,
    meta: MarketMetadata,
    candles: dict[str, list[Candle]],
    acc: _CalibrationAccumulator,
) -> None:
    """Replay a single market window through the engine.

    Args:
        config: Algorithm configuration.
        repo: Tick repository for order book snapshots.
        meta: Market metadata for this window.
        candles: Pre-loaded candles keyed by asset.
        acc: Accumulator for metrics.

    """
    opp = _metadata_to_opportunity(meta)

    asset_candles = candles.get(meta.asset, [])
    window_candles = [
        c for c in asset_candles if meta.window_start_ts <= c.timestamp <= meta.window_end_ts
    ]
    outcome = _determine_outcome(window_candles)

    if outcome is None:
        acc.skipped += 1
        return

    replay_md = ReplayMarketData()
    replay_md.set_markets([opp])
    replay_md.set_candles(meta.asset, asset_candles)
    replay_md.set_outcome(meta.condition_id, outcome)

    # Set default order books (real snapshots used in future enhancement)
    up_book = _make_default_book(meta.up_token_id)
    down_book = _make_default_book(meta.down_token_id)
    replay_md.set_order_books(meta.condition_id, up_book, down_book)

    execution = BacktestExecution(capital=config.capital + acc.total_pnl)
    estimator = ProbabilityEstimator(config)

    engine = DirectionalEngine(
        config=config,
        execution=execution,
        market_data=replay_md,
        estimator=estimator,
        mode_label="BACKTEST",
    )

    # Step clock through the window
    t = meta.window_start_ts
    while t <= meta.window_end_ts + 1:
        await engine.poll_cycle(t)
        t += config.poll_interval

    # Final settle
    await engine.poll_cycle(meta.window_end_ts + 1)

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
