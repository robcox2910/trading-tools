"""Grid search over directional trading algorithm parameters.

Sweep estimator weights, entry window timing, min_edge, and Kelly
fraction across a grid, replaying each combination against historical
data via ``run_directional_backtest``.  Collect per-cell performance
and calibration metrics.  Support walk-forward validation by splitting
metadata chronologically into train/test sets.
"""

from __future__ import annotations

import dataclasses
import itertools
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from trading_tools.apps.directional.backtest_runner import (
    BookSnapshotCache,
    WhaleTradeCache,
    run_directional_backtest,
)
from trading_tools.core.models import ZERO

if TYPE_CHECKING:
    from trading_tools.apps.directional.config import DirectionalConfig
    from trading_tools.apps.tick_collector.repository import TickRepository
    from trading_tools.apps.whale_monitor.repository import WhaleRepository
    from trading_tools.core.models import Candle

logger = logging.getLogger(__name__)

_HUNDRED = Decimal(100)


@dataclass(frozen=True)
class DirectionalGridCell:
    """Performance metrics for a single parameter combination.

    Attributes:
        params: Dictionary of parameter names to values swept for this cell.
        return_pct: Total return as a percentage of initial capital.
        total_trades: Number of positions entered.
        wins: Number of winning positions.
        losses: Number of losing positions.
        win_rate: Fraction of profitable trades (0-1).
        brier_score: Mean squared error of probability predictions.
        avg_pnl: Average P&L per trade.
        skipped: Windows skipped.

    """

    params: dict[str, object]
    return_pct: Decimal
    total_trades: int
    wins: int
    losses: int
    win_rate: Decimal
    brier_score: Decimal
    avg_pnl: Decimal
    skipped: int


@dataclass(frozen=True)
class DirectionalGridResult:
    """Aggregate result from a directional grid search.

    Attributes:
        cells: Flat list of per-cell results, sorted by brier_score ascending.
        total_windows: Number of market windows in the dataset.
        initial_capital: Starting capital for each cell.

    """

    cells: tuple[DirectionalGridCell, ...]
    total_windows: int
    initial_capital: Decimal


async def run_directional_grid(
    base_config: DirectionalConfig,
    repo: TickRepository,
    start_ts: int,
    end_ts: int,
    param_grid: dict[str, list[object]],
    *,
    candles_by_asset: dict[str, list[Candle]] | None = None,
    series_slug: str | None = None,
    whale_repo: WhaleRepository | None = None,
) -> DirectionalGridResult:
    """Sweep parameter combinations and collect performance metrics.

    Generate the Cartesian product of all parameter values in
    ``param_grid``, run a backtest for each combination, and return
    the results sorted by Brier score (lower is better).

    Args:
        base_config: Base configuration to override per-cell.
        repo: Tick repository for loading historical data.
        start_ts: Start epoch seconds (inclusive).
        end_ts: End epoch seconds (inclusive).
        param_grid: Mapping of config field names to lists of values
            to sweep (e.g. ``{"w_whale": [0.3, 0.4, 0.5]}``).
        candles_by_asset: Pre-loaded Binance candles keyed by asset.
        series_slug: Filter metadata to a specific series slug.
        whale_repo: Whale trade repository for directional signals.

    Returns:
        Grid result with cells sorted by Brier score ascending.

    """
    # Build all parameter combinations
    param_names = list(param_grid.keys())
    param_values = list(param_grid.values())
    combos = list(itertools.product(*param_values))

    logger.info(
        "Directional grid search: %d combinations across %s",
        len(combos),
        ", ".join(param_names),
    )

    # Pre-fetch shared data once instead of per-combo
    metadata_list = await repo.get_market_metadata_in_range(
        start_ts, end_ts, series_slug=series_slug
    )
    logger.info("Grid pre-fetch: %d market windows", len(metadata_list))

    # Bulk-load order book snapshots for the entire time range
    _ms_per_second = 1000
    start_ms = start_ts * _ms_per_second
    end_ms = end_ts * _ms_per_second
    all_snapshots = await repo.get_all_book_snapshots_in_range(start_ms, end_ms)
    snapshot_cache = BookSnapshotCache(all_snapshots)
    logger.info("Grid pre-fetch: %d order book snapshots cached", len(all_snapshots))

    # Bulk-load whale trades for all condition IDs
    whale_cache: WhaleTradeCache | None = None
    if whale_repo is not None:
        condition_ids = {m.condition_id for m in metadata_list}
        all_trades = await whale_repo.get_buy_trades_for_conditions(condition_ids)
        whale_cache = WhaleTradeCache(all_trades)
        logger.info("Grid pre-fetch: %d whale BUY trades cached", len(all_trades))

    cells: list[DirectionalGridCell] = []
    total_windows = len(metadata_list)

    for i, combo in enumerate(combos):
        overrides = dict(zip(param_names, combo, strict=True))
        config = dataclasses.replace(base_config, **overrides)

        result = await run_directional_backtest(
            config=config,
            repo=repo,
            start_ts=start_ts,
            end_ts=end_ts,
            candles_by_asset=candles_by_asset,
            series_slug=series_slug,
            metadata_list=metadata_list,
            snapshot_cache=snapshot_cache,
            whale_cache=whale_cache,
        )

        cell = DirectionalGridCell(
            params=overrides,
            return_pct=result.return_pct,
            total_trades=result.total_trades,
            wins=result.wins,
            losses=result.losses,
            win_rate=result.win_rate,
            brier_score=result.brier_score,
            avg_pnl=result.avg_pnl,
            skipped=result.skipped,
        )
        cells.append(cell)

        logger.info(
            "Grid [%d/%d] %s → trades=%d win=%.0f%% brier=%.4f return=%.1f%%",
            i + 1,
            len(combos),
            " ".join(f"{k}={v}" for k, v in overrides.items()),
            result.total_trades,
            float(result.win_rate * _HUNDRED),
            result.brier_score,
            result.return_pct,
        )

    # Sort by Brier score ascending (lower = better calibration)
    cells.sort(key=lambda c: c.brier_score if c.brier_score > ZERO else Decimal(999))

    return DirectionalGridResult(
        cells=tuple(cells),
        total_windows=total_windows,
        initial_capital=base_config.capital,
    )


def format_grid_table(result: DirectionalGridResult, top_n: int = 20) -> str:
    """Format grid search results as a markdown table.

    Args:
        result: Completed grid search result.
        top_n: Maximum number of rows to include.

    Returns:
        Markdown-formatted table string.

    """
    if not result.cells:
        return "No results."

    # Collect all param names from the first cell
    param_names = list(result.cells[0].params.keys())
    header_parts = [*param_names, "Trades", "Wins", "Losses", "Win%", "Brier", "Return%", "AvgPnL"]
    header = "| " + " | ".join(header_parts) + " |"
    separator = "| " + " | ".join("---" for _ in header_parts) + " |"

    rows = [header, separator]
    for cell in result.cells[:top_n]:
        param_vals = [str(cell.params[p]) for p in param_names]
        row_parts = [
            *param_vals,
            str(cell.total_trades),
            str(cell.wins),
            str(cell.losses),
            f"{cell.win_rate * _HUNDRED:.0f}",
            f"{cell.brier_score:.4f}",
            f"{cell.return_pct:.1f}",
            f"{cell.avg_pnl:.4f}",
        ]
        rows.append("| " + " | ".join(row_parts) + " |")

    return "\n".join(rows)
