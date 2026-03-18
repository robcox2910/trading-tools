"""Replay engine for backtesting the spread capture strategy.

Load historical market metadata, tick data, and order book snapshots
from the database, reconstruct ``SpreadOpportunity`` objects, and feed
them through the ``SpreadEngine`` with a ``ReplayMarketData`` adapter
and ``BacktestExecution`` adapter.  Step the clock from window start
to window end at the configured poll interval.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from trading_tools.apps.spread_capture.adapters import (
    BacktestExecution,
    ReplayMarketData,
)
from trading_tools.apps.spread_capture.engine import SpreadEngine
from trading_tools.apps.spread_capture.models import SpreadOpportunity
from trading_tools.core.models import ZERO

if TYPE_CHECKING:
    from collections.abc import Sequence

    from trading_tools.apps.spread_capture.config import SpreadCaptureConfig
    from trading_tools.apps.tick_collector.models import MarketMetadata, OrderBookSnapshot
    from trading_tools.apps.tick_collector.repository import TickRepository
    from trading_tools.core.models import Candle

logger = logging.getLogger(__name__)

_MS_PER_SECOND = 1000
_HUNDRED = Decimal(100)


@dataclass(frozen=True)
class SpreadBacktestResult:
    """Aggregate result from a spread capture backtest run.

    Attributes:
        initial_capital: Starting virtual capital.
        final_capital: Ending capital after all settlements.
        total_pnl: Net P&L across all windows.
        return_pct: Return as a percentage of initial capital.
        total_windows: Number of market windows replayed.
        total_trades: Number of positions with at least one fill.
        wins: Positions with positive P&L.
        losses: Positions with negative P&L.
        win_rate: Fraction of profitable positions (0-1).
        avg_pnl: Average P&L per settled position.

    """

    initial_capital: Decimal
    final_capital: Decimal
    total_pnl: Decimal
    return_pct: Decimal
    total_windows: int
    total_trades: int
    wins: int
    losses: int
    win_rate: Decimal
    avg_pnl: Decimal


def _metadata_to_opportunity(meta: MarketMetadata) -> SpreadOpportunity:
    """Convert a ``MarketMetadata`` record to a ``SpreadOpportunity``.

    The initial prices are set to 0.50/0.50 since the actual prices
    come from order book snapshots during replay.

    Args:
        meta: Market metadata from the database.

    Returns:
        A ``SpreadOpportunity`` with metadata fields populated.

    """
    half = Decimal("0.50")
    return SpreadOpportunity(
        condition_id=meta.condition_id,
        title=meta.title,
        asset=meta.asset,
        up_token_id=meta.up_token_id,
        down_token_id=meta.down_token_id,
        up_price=half,
        down_price=half,
        combined=Decimal("1.00"),
        margin=ZERO,
        window_start_ts=meta.window_start_ts,
        window_end_ts=meta.window_end_ts,
    )


def _group_books_by_token(
    snapshots: list[OrderBookSnapshot],
    up_token_id: str,
    down_token_id: str,
) -> tuple[dict[int, OrderBookSnapshot], dict[int, OrderBookSnapshot]]:
    """Split order book snapshots into up and down books keyed by timestamp.

    Args:
        snapshots: All order book snapshots for the relevant tokens.
        up_token_id: CLOB token ID for the Up outcome.
        down_token_id: CLOB token ID for the Down outcome.

    Returns:
        Tuple of (up_books, down_books) dicts keyed by timestamp_ms.

    """
    up_books: dict[int, OrderBookSnapshot] = {}
    down_books: dict[int, OrderBookSnapshot] = {}
    for snap in snapshots:
        if snap.token_id == up_token_id:
            up_books[snap.timestamp] = snap
        elif snap.token_id == down_token_id:
            down_books[snap.timestamp] = snap
    return up_books, down_books


def _determine_outcome(
    candles: Sequence[Candle],
) -> str | None:
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


async def run_spread_backtest(
    config: SpreadCaptureConfig,
    repo: TickRepository,
    start_ts: int,
    end_ts: int,
    *,
    candles_by_asset: dict[str, list[Candle]] | None = None,
    series_slug: str | None = None,
) -> SpreadBacktestResult:
    """Run a spread capture backtest over a date range.

    Load market metadata and order book snapshots from the database,
    then replay each market window through the ``SpreadEngine``.

    Args:
        config: Strategy configuration for the backtest.
        repo: Tick repository for loading historical data.
        start_ts: Start epoch seconds (inclusive).
        end_ts: End epoch seconds (inclusive).
        candles_by_asset: Pre-loaded Binance candles keyed by asset.
            When ``None``, the engine falls back to the opportunity
            price for the momentum signal.
        series_slug: Filter metadata to a specific series slug.

    Returns:
        Aggregate backtest result with performance metrics.

    """
    metadata_list = await repo.get_market_metadata_in_range(
        start_ts, end_ts, series_slug=series_slug
    )

    if not metadata_list:
        logger.warning("No market metadata found for %d - %d", start_ts, end_ts)
        return SpreadBacktestResult(
            initial_capital=config.capital,
            final_capital=config.capital,
            total_pnl=ZERO,
            return_pct=ZERO,
            total_windows=0,
            total_trades=0,
            wins=0,
            losses=0,
            win_rate=ZERO,
            avg_pnl=ZERO,
        )

    logger.info("Loaded %d market windows for backtest", len(metadata_list))

    candles = candles_by_asset or {}
    total_pnl = ZERO
    wins = 0
    losses = 0
    total_trades = 0

    for meta in metadata_list:
        opp = _metadata_to_opportunity(meta)

        # Load order book snapshots for both tokens
        start_ms = meta.window_start_ts * _MS_PER_SECOND
        end_ms = meta.window_end_ts * _MS_PER_SECOND
        all_books = await repo.get_order_book_snapshots_in_range(meta.up_token_id, start_ms, end_ms)
        down_books_raw = await repo.get_order_book_snapshots_in_range(
            meta.down_token_id, start_ms, end_ms
        )
        all_books.extend(down_books_raw)

        up_books, down_books = _group_books_by_token(
            all_books, meta.up_token_id, meta.down_token_id
        )

        # Determine outcome for this window
        asset_candles = candles.get(meta.asset, [])
        window_candles = [
            c for c in asset_candles if meta.window_start_ts <= c.timestamp <= meta.window_end_ts
        ]
        outcome = _determine_outcome(window_candles)

        # Build replay adapters
        replay_md = ReplayMarketData(
            opportunities=[opp],
            up_books=up_books,
            down_books=down_books,
            candles=candles,
            outcome=outcome,
        )

        engine = SpreadEngine(
            config=config,
            execution=BacktestExecution(  # type: ignore[arg-type]
                capital=config.capital + total_pnl,
                slippage_pct=config.paper_slippage_pct,
            ),
            market_data=replay_md,  # type: ignore[arg-type]
            mode_label="BACKTEST",
        )
        engine.init_capital()

        # Step clock through the window at poll_interval
        t = meta.window_start_ts
        while t <= meta.window_end_ts:
            replay_md.clock = t
            await engine.poll_cycle(t)
            t += config.poll_interval

        # Final settle at window end
        replay_md.clock = meta.window_end_ts + 1
        await engine.poll_cycle(meta.window_end_ts + 1)

        # Collect results
        for result in engine.results:
            total_pnl += result.pnl
            if result.pnl > ZERO:
                wins += 1
            elif result.pnl < ZERO:
                losses += 1
            if result.up_qty > ZERO or (result.down_qty is not None and result.down_qty > ZERO):
                total_trades += 1

        logger.info(
            "WINDOW %s %s→%s outcome=%s pnl=%.4f trades=%d",
            meta.asset,
            meta.window_start_ts,
            meta.window_end_ts,
            outcome,
            sum(r.pnl for r in engine.results),
            len(engine.results),
        )

    final_capital = config.capital + total_pnl
    return_pct = (total_pnl / config.capital * _HUNDRED) if config.capital > ZERO else ZERO
    win_rate = Decimal(wins) / Decimal(wins + losses) if (wins + losses) > 0 else ZERO
    avg_pnl = total_pnl / Decimal(total_trades) if total_trades > 0 else ZERO

    return SpreadBacktestResult(
        initial_capital=config.capital,
        final_capital=final_capital,
        total_pnl=total_pnl,
        return_pct=return_pct,
        total_windows=len(metadata_list),
        total_trades=total_trades,
        wins=wins,
        losses=losses,
        win_rate=win_rate,
        avg_pnl=avg_pnl,
    )
