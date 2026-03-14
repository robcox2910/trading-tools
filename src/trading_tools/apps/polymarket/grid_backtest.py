"""Grid search over late snipe strategy parameters with liquidity checking.

Sweep threshold and window parameters across a grid, replay each combination
against real tick data with order book liquidity validation, and collect
per-cell performance metrics. Results are formatted as a markdown table
for quick visual comparison.
"""

from __future__ import annotations

import logging
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from trading_tools.apps.polymarket.backtest_common import (
    build_backtest_result,
    compute_resolved_outcomes,
    feed_snapshot_to_strategy,
    resolve_positions,
)
from trading_tools.apps.polymarket_bot.portfolio import PaperPortfolio
from trading_tools.apps.polymarket_bot.strategies.late_snipe import PMLateSnipeStrategy
from trading_tools.apps.tick_collector.snapshot_builder import (
    MarketWindow,
    SnapshotBuilder,
)
from trading_tools.core.models import ZERO

if TYPE_CHECKING:
    from trading_tools.apps.polymarket_bot.models import MarketSnapshot
    from trading_tools.apps.tick_collector.models import OrderBookSnapshot, Tick

logger = logging.getLogger(__name__)

_MS_PER_SECOND = 1000
_HUNDRED = Decimal(100)

# Module-level state shared across tasks within each worker process.
# Set once per worker via _init_grid_worker to avoid pickling window_data
# for every task submission (108 tasks x ~320MB = ~35GB otherwise).
_worker_window_data: list[tuple[MarketWindow, list[MarketSnapshot]]] | None = None
_worker_resolved_outcomes: dict[str, bool] | None = None


def _init_grid_worker(
    window_data: list[tuple[MarketWindow, list[MarketSnapshot]]],
    resolved_outcomes: dict[str, bool] | None,
) -> None:
    """Set shared data in each worker process once at startup.

    Called by ``ProcessPoolExecutor(initializer=...)`` so that the large
    ``window_data`` structure is pickled only once per worker (typically
    4-8 workers) instead of once per task (108+ tasks).

    Args:
        window_data: Pre-built (MarketWindow, snapshots) pairs.
        resolved_outcomes: Precomputed mapping from condition_id to
            ``True`` if YES won.

    """
    global _worker_window_data, _worker_resolved_outcomes  # noqa: PLW0603
    _worker_window_data = window_data
    _worker_resolved_outcomes = resolved_outcomes


def _run_cell_worker(
    threshold: Decimal,
    window_seconds: int,
    capital: Decimal,
    kelly_frac: Decimal,
    max_position_pct: Decimal,
    max_slippage: Decimal | None,
) -> GridCell:
    """Run a single grid cell using shared worker-process state.

    Read ``window_data`` and ``resolved_outcomes`` from module-level
    globals set by ``_init_grid_worker`` rather than receiving them as
    arguments, avoiding the pickle overhead per task submission.

    Args:
        threshold: Snipe price threshold.
        window_seconds: Snipe window duration in seconds.
        capital: Initial virtual capital.
        kelly_frac: Fractional Kelly multiplier.
        max_position_pct: Maximum fraction of capital per market.
        max_slippage: Maximum allowable slippage from the snapshot price.

    Returns:
        A ``GridCell`` with the performance metrics for this combination.

    """
    assert _worker_window_data is not None  # noqa: S101
    return _run_single_cell(
        _worker_window_data,
        threshold,
        window_seconds,
        capital,
        kelly_frac,
        max_position_pct,
        max_slippage,
        _worker_resolved_outcomes,
    )


@dataclass(frozen=True)
class GridCell:
    """Performance metrics for a single (threshold, window) combination.

    Capture the return percentage, trade counts, and win rate produced
    by replaying the late snipe strategy with specific parameters.

    Args:
        threshold: Snipe price threshold used for this cell.
        window_seconds: Snipe window duration in seconds.
        return_pct: Total return as a percentage of initial capital.
        total_trades: Number of positions opened.
        wins: Number of winning positions.
        losses: Number of losing positions.
        win_rate: Fraction of trades that were winners (0-1), or zero
            if no trades were placed.

    """

    threshold: Decimal
    window_seconds: int
    return_pct: Decimal
    total_trades: int
    wins: int
    losses: int
    win_rate: Decimal


@dataclass(frozen=True)
class GridBacktestResult:
    """Aggregate result from a full grid search backtest.

    Bundle the per-cell results with the grid axes and metadata needed
    to produce formatted output tables.

    Args:
        cells: Flat list of ``GridCell`` results, one per parameter combination.
        thresholds: Sorted list of threshold values searched.
        windows: Sorted list of window durations (seconds) searched.
        initial_capital: Starting capital used for each cell.
        total_conditions: Number of distinct markets in the dataset.
        total_ticks: Total tick count across all markets.

    """

    cells: tuple[GridCell, ...]
    thresholds: tuple[Decimal, ...]
    windows: tuple[int, ...]
    initial_capital: Decimal
    total_conditions: int
    total_ticks: int


def _build_window_data(
    all_ticks: dict[str, list[Tick]],
    bucket_seconds: int,
    window_minutes: int,
    book_snapshots: dict[str, list[OrderBookSnapshot]] | None,
) -> list[tuple[MarketWindow, list[MarketSnapshot]]]:
    """Pre-build window/snapshot pairs from tick data.

    Construct the shared data structure once so that each grid cell can
    replay the same sequence without repeating the snapshot building work.

    Args:
        all_ticks: Mapping from condition_id to sorted ticks.
        bucket_seconds: Seconds per snapshot bucket.
        window_minutes: Duration of each market window in minutes.
        book_snapshots: Optional order book snapshot data for enrichment.

    Returns:
        List of (MarketWindow, snapshots) pairs ready for replay.

    """
    builder = SnapshotBuilder(bucket_seconds=bucket_seconds, window_minutes=window_minutes)
    window_data: list[tuple[MarketWindow, list[MarketSnapshot]]] = []
    for condition_id, ticks in sorted(all_ticks.items()):
        for window, window_ticks in builder.detect_all_windows(condition_id, ticks):
            snapshots = builder.build_snapshots(
                window_ticks,
                window,
                book_snapshots=book_snapshots,
            )
            window_data.append((window, snapshots))
    return window_data


def _run_single_cell(
    window_data: list[tuple[MarketWindow, list[MarketSnapshot]]],
    threshold: Decimal,
    window_seconds: int,
    capital: Decimal,
    kelly_frac: Decimal,
    max_position_pct: Decimal,
    max_slippage: Decimal | None = None,
    resolved_outcomes: dict[str, bool] | None = None,
) -> GridCell:
    """Run a single backtest cell with specific strategy parameters.

    Create a fresh strategy and portfolio, replay all windows with
    liquidity checking enabled, and collect performance metrics.

    Args:
        window_data: Pre-built (MarketWindow, snapshots) pairs.
        threshold: Snipe price threshold.
        window_seconds: Snipe window duration in seconds.
        capital: Initial virtual capital.
        kelly_frac: Fractional Kelly multiplier.
        max_position_pct: Maximum fraction of capital per market.
        max_slippage: Maximum allowable slippage from the snapshot price.
            When ``None``, slippage modelling is disabled.
        resolved_outcomes: Precomputed mapping from condition_id to
            ``True`` if YES won. When provided, overrides the
            within-window last-tick price heuristic for resolution.

    Returns:
        A ``GridCell`` with the performance metrics for this combination.

    """
    strategy = PMLateSnipeStrategy(threshold=threshold, window_seconds=window_seconds)
    portfolio = PaperPortfolio(capital, max_position_pct)
    position_outcomes: dict[str, str] = {}
    snapshots_processed = 0
    windows_processed = 0
    wins = 0
    losses = 0

    for window, snapshots in window_data:
        windows_processed += 1
        for snapshot in snapshots:
            snapshots_processed += 1
            feed_snapshot_to_strategy(
                snapshot=snapshot,
                strategy=strategy,
                portfolio=portfolio,
                kelly_frac=kelly_frac,
                position_outcomes=position_outcomes,
                check_liquidity=True,
                max_slippage=max_slippage,
            )

        final_prices: dict[str, Decimal] = {}
        if snapshots:
            final_prices[window.condition_id] = snapshots[-1].yes_price

        resolve_ts = window.end_ms // _MS_PER_SECOND
        cell_wins, cell_losses = resolve_positions(
            portfolio=portfolio,
            position_outcomes=position_outcomes,
            final_prices=final_prices,
            resolve_ts=resolve_ts,
            resolved_outcomes=resolved_outcomes,
        )
        wins += cell_wins
        losses += cell_losses

    result = build_backtest_result(
        strategy_name=strategy.name,
        initial_capital=capital,
        portfolio=portfolio,
        snapshots_processed=snapshots_processed,
        windows_processed=windows_processed,
        wins=wins,
        losses=losses,
    )

    total_trades = int(result.metrics.get("total_trades", ZERO))
    return_pct = ZERO
    if capital > ZERO:
        return_pct = (result.final_capital - capital) / capital * _HUNDRED

    win_rate = ZERO
    if wins + losses > 0:
        win_rate = Decimal(wins) / Decimal(wins + losses)

    return GridCell(
        threshold=threshold,
        window_seconds=window_seconds,
        return_pct=return_pct,
        total_trades=total_trades,
        wins=wins,
        losses=losses,
        win_rate=win_rate,
    )


def run_grid_backtest(
    all_ticks: dict[str, list[Tick]],
    book_snapshots: dict[str, list[OrderBookSnapshot]] | None,
    thresholds: list[Decimal],
    windows: list[int],
    *,
    capital: Decimal,
    kelly_frac: Decimal,
    max_position_pct: Decimal,
    bucket_seconds: int,
    window_minutes: int = 5,
    max_slippage: Decimal | None = None,
) -> GridBacktestResult:
    """Run a grid search over threshold and window parameters.

    Pre-build snapshot data once, then loop over all (threshold, window)
    combinations with a fresh strategy and portfolio for each. Liquidity
    checking is always enabled.

    Args:
        all_ticks: Mapping from condition_id to sorted ticks.
        book_snapshots: Optional order book snapshot data for enrichment
            and liquidity checking.
        thresholds: List of snipe threshold values to search.
        windows: List of snipe window durations (seconds) to search.
        capital: Initial virtual capital per cell.
        kelly_frac: Fractional Kelly multiplier.
        max_position_pct: Maximum fraction of capital per market.
        bucket_seconds: Seconds per snapshot bucket.
        window_minutes: Duration of each market window in minutes.
        max_slippage: Maximum allowable slippage from the snapshot price.
            When ``None``, slippage modelling is disabled.

    Returns:
        A ``GridBacktestResult`` with all cell results and metadata.

    """
    window_data = _build_window_data(
        all_ticks,
        bucket_seconds=bucket_seconds,
        window_minutes=window_minutes,
        book_snapshots=book_snapshots,
    )

    resolved_outcomes = compute_resolved_outcomes(all_ticks)

    total_combos = len(thresholds) * len(windows)
    logger.info("Running %d grid combinations...", total_combos)

    combos = [(t, w) for t in sorted(thresholds) for w in sorted(windows, reverse=True)]

    cells: list[GridCell] = []
    with ProcessPoolExecutor(
        initializer=_init_grid_worker,
        initargs=(window_data, resolved_outcomes),
    ) as executor:
        futures = {
            executor.submit(
                _run_cell_worker,
                t,
                w,
                capital,
                kelly_frac,
                max_position_pct,
                max_slippage,
            ): (t, w)
            for t, w in combos
        }
        for future in as_completed(futures):
            cell = future.result()
            cells.append(cell)
            logger.info(
                "[%d/%d] threshold=%.2f window=%ds → return=%.2f%% trades=%d",
                len(cells),
                total_combos,
                cell.threshold,
                cell.window_seconds,
                cell.return_pct,
                cell.total_trades,
            )

    cells.sort(key=lambda c: (c.threshold, -c.window_seconds))

    total_conditions = len(all_ticks)
    total_ticks = sum(len(t) for t in all_ticks.values())

    return GridBacktestResult(
        cells=tuple(cells),
        thresholds=tuple(sorted(thresholds)),
        windows=tuple(sorted(windows, reverse=True)),
        initial_capital=capital,
        total_conditions=total_conditions,
        total_ticks=total_ticks,
    )


def format_grid_table(result: GridBacktestResult, metric: str = "return_pct") -> str:
    """Format grid results as a threshold x window markdown table.

    Build a markdown-formatted table with thresholds as rows and window
    durations as columns. Each cell shows the specified metric value.

    Args:
        result: Completed grid backtest result.
        metric: Which ``GridCell`` field to display. One of ``"return_pct"``,
            ``"total_trades"``, ``"win_rate"``, ``"wins"``, ``"losses"``.

    Returns:
        A string containing the formatted markdown table.

    """
    cell_map: dict[tuple[Decimal, int], GridCell] = {
        (c.threshold, c.window_seconds): c for c in result.cells
    }

    # Header row
    header_parts = ["| Threshold"]
    header_parts.extend(f" {w}s" for w in result.windows)
    header = " |".join(header_parts) + " |"

    # Separator row
    sep_parts = ["|---"]
    sep_parts.extend("---" for _ in result.windows)
    sep = "|".join(sep_parts) + "|"

    # Data rows
    rows: list[str] = []
    for threshold in result.thresholds:
        parts = [f"| {threshold:.2f}"]
        for w in result.windows:
            cell = cell_map.get((threshold, w))
            if cell is None:
                parts.append(" -")
            else:
                value = getattr(cell, metric)
                if metric == "return_pct":
                    parts.append(f" {value:.1f}%")
                elif metric == "win_rate":
                    parts.append(f" {value * _HUNDRED:.1f}%")
                else:
                    parts.append(f" {value}")
        rows.append(" |".join(parts) + " |")

    return "\n".join([header, sep, *rows])
