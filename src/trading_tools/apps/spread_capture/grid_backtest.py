"""Grid search over spread capture strategy parameters.

Sweep hedge threshold, signal delay, and other parameters across a grid,
replay each combination against historical data, and collect per-cell
performance metrics.  Results are formatted as markdown tables for quick
visual comparison.
"""

from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from trading_tools.apps.spread_capture.backtest_runner import run_spread_backtest

if TYPE_CHECKING:
    from trading_tools.apps.spread_capture.config import SpreadCaptureConfig
    from trading_tools.apps.tick_collector.repository import TickRepository
    from trading_tools.core.models import Candle

logger = logging.getLogger(__name__)

_HUNDRED = Decimal(100)


@dataclass(frozen=True)
class SpreadGridCell:
    """Performance metrics for a single parameter combination.

    Attributes:
        hedge_start: Hedge start threshold used.
        hedge_end: Hedge end threshold used.
        signal_delay: Signal delay in seconds.
        return_pct: Total return as a percentage of initial capital.
        total_trades: Number of positions opened.
        wins: Number of winning positions.
        losses: Number of losing positions.
        win_rate: Fraction of trades that were winners (0-1).

    """

    hedge_start: Decimal
    hedge_end: Decimal
    signal_delay: int
    return_pct: Decimal
    total_trades: int
    wins: int
    losses: int
    win_rate: Decimal


@dataclass(frozen=True)
class SpreadGridResult:
    """Aggregate result from a spread capture grid search.

    Attributes:
        cells: Flat list of per-cell results.
        hedge_starts: Sorted hedge start values searched.
        hedge_ends: Sorted hedge end values searched.
        signal_delays: Sorted signal delay values searched.
        initial_capital: Starting capital used for each cell.
        total_windows: Total market windows in the dataset.

    """

    cells: tuple[SpreadGridCell, ...]
    hedge_starts: tuple[Decimal, ...]
    hedge_ends: tuple[Decimal, ...]
    signal_delays: tuple[int, ...]
    initial_capital: Decimal
    total_windows: int


async def run_spread_grid(
    base_config: SpreadCaptureConfig,
    repo: TickRepository,
    start_ts: int,
    end_ts: int,
    hedge_starts: list[Decimal],
    hedge_ends: list[Decimal],
    signal_delays: list[int],
    *,
    candles_by_asset: dict[str, list[Candle]] | None = None,
    series_slug: str | None = None,
) -> SpreadGridResult:
    """Run a grid search over spread capture parameters.

    For each (hedge_start, hedge_end, signal_delay) combination, run
    a full backtest and collect performance metrics.

    Args:
        base_config: Base configuration with non-swept parameters.
        repo: Tick repository for loading historical data.
        start_ts: Start epoch seconds.
        end_ts: End epoch seconds.
        hedge_starts: List of hedge start thresholds to sweep.
        hedge_ends: List of hedge end thresholds to sweep.
        signal_delays: List of signal delay values to sweep.
        candles_by_asset: Pre-loaded Binance candles keyed by asset.
        series_slug: Filter to a specific series slug.

    Returns:
        A ``SpreadGridResult`` with all cell results and metadata.

    """
    total_combos = len(hedge_starts) * len(hedge_ends) * len(signal_delays)
    logger.info("Running %d grid combinations...", total_combos)

    cells: list[SpreadGridCell] = []
    cell_num = 0

    for hs in sorted(hedge_starts):
        for he in sorted(hedge_ends):
            if he <= hs:
                continue  # Skip invalid: hedge_end must exceed hedge_start
            for sd in sorted(signal_delays):
                cell_num += 1

                cell_config = dataclasses.replace(
                    base_config,
                    hedge_start_threshold=hs,
                    hedge_end_threshold=he,
                    signal_delay_seconds=sd,
                )

                result = await run_spread_backtest(
                    config=cell_config,
                    repo=repo,
                    start_ts=start_ts,
                    end_ts=end_ts,
                    candles_by_asset=candles_by_asset,
                    series_slug=series_slug,
                )

                cell = SpreadGridCell(
                    hedge_start=hs,
                    hedge_end=he,
                    signal_delay=sd,
                    return_pct=result.return_pct,
                    total_trades=result.total_trades,
                    wins=result.wins,
                    losses=result.losses,
                    win_rate=result.win_rate,
                )
                cells.append(cell)

                logger.info(
                    "[%d/%d] hedge=%.2f→%.2f delay=%ds → return=%.2f%% trades=%d",
                    cell_num,
                    total_combos,
                    hs,
                    he,
                    sd,
                    cell.return_pct,
                    cell.total_trades,
                )

    total_windows = 0
    if cells:
        total_windows = cells[0].total_trades  # approximate from first cell

    return SpreadGridResult(
        cells=tuple(cells),
        hedge_starts=tuple(sorted(hedge_starts)),
        hedge_ends=tuple(sorted(hedge_ends)),
        signal_delays=tuple(sorted(signal_delays)),
        initial_capital=base_config.capital,
        total_windows=total_windows,
    )


def format_spread_grid_table(
    result: SpreadGridResult,
    metric: str = "return_pct",
) -> str:
    """Format grid results as a hedge_start x hedge_end markdown table.

    Build a markdown-formatted table with hedge_start as rows and
    hedge_end as columns.  When multiple signal_delays are present,
    show the best (highest return) for each cell.

    Args:
        result: Completed grid search result.
        metric: Which ``SpreadGridCell`` field to display.

    Returns:
        A string containing the formatted markdown table.

    """
    # Group cells by (hedge_start, hedge_end), picking best signal_delay
    best_cells: dict[tuple[Decimal, Decimal], SpreadGridCell] = {}
    for cell in result.cells:
        key = (cell.hedge_start, cell.hedge_end)
        existing = best_cells.get(key)
        if existing is None or cell.return_pct > existing.return_pct:
            best_cells[key] = cell

    # Header row
    header_parts = ["| Hedge Start"]
    header_parts.extend(f" {he:.2f}" for he in result.hedge_ends)
    header = " |".join(header_parts) + " |"

    # Separator row
    sep_parts = ["|---"]
    sep_parts.extend("---" for _ in result.hedge_ends)
    sep = "|".join(sep_parts) + "|"

    # Data rows
    rows: list[str] = []
    for hs in result.hedge_starts:
        parts = [f"| {hs:.2f}"]
        for he in result.hedge_ends:
            cell = best_cells.get((hs, he))
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
