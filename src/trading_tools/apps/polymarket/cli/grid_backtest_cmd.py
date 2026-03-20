"""CLI command for grid-searching late snipe strategy parameters.

Sweep threshold and window parameters on 5-minute markets, replaying
each combination against real tick data with order book liquidity
validation. Display results as threshold x window markdown tables.
"""

from __future__ import annotations

import asyncio
import os
from decimal import Decimal
from typing import TYPE_CHECKING, Annotated

import typer

from trading_tools.apps.polymarket.backtest_common import (
    configure_verbose_logging,
    load_ticks,
    parse_date,
)
from trading_tools.apps.polymarket.grid_backtest import (
    format_grid_table,
    run_grid_backtest,
)

if TYPE_CHECKING:
    from trading_tools.apps.polymarket.grid_backtest import GridBacktestResult

_MS_PER_SECOND = 1000
_DEFAULT_DB_URL = os.environ.get("TICK_DB_URL", "sqlite+aiosqlite:///tick_data.db")
_WINDOW_MINUTES = 5

_THRESHOLD_START = Decimal("0.55")
_THRESHOLD_END = Decimal("0.95")
_THRESHOLD_STEP = Decimal("0.05")
_WINDOW_START = 120
_WINDOW_END = 10
_WINDOW_STEP = 10


def _build_threshold_grid() -> list[Decimal]:
    """Build the list of snipe threshold values to search.

    Returns:
        Sorted threshold values from 0.55 to 0.95 in 0.05 increments.

    """
    thresholds: list[Decimal] = []
    t = _THRESHOLD_START
    while t <= _THRESHOLD_END:
        thresholds.append(t)
        t += _THRESHOLD_STEP
    return thresholds


def _build_window_grid() -> list[int]:
    """Build the list of snipe window durations to search.

    Returns:
        Window durations from 120s down to 10s in 10s decrements.

    """
    windows: list[int] = []
    w = _WINDOW_START
    while w >= _WINDOW_END:
        windows.append(w)
        w -= _WINDOW_STEP
    return windows


def _display_results(result: GridBacktestResult) -> None:
    """Display the grid backtest results as markdown tables.

    Args:
        result: Completed grid backtest result.

    """
    typer.echo("=== Return % ===")
    typer.echo(format_grid_table(result, metric="return_pct"))
    typer.echo("")
    typer.echo("=== Total Trades ===")
    typer.echo(format_grid_table(result, metric="total_trades"))
    typer.echo("")
    typer.echo("=== Win Rate ===")
    typer.echo(format_grid_table(result, metric="win_rate"))


def grid_backtest(
    start: Annotated[str, typer.Option(help="Start date YYYY-MM-DD")] = "",
    end: Annotated[str, typer.Option(help="End date YYYY-MM-DD")] = "",
    db_url: Annotated[
        str, typer.Option(help="SQLAlchemy async DB URL for tick data")
    ] = _DEFAULT_DB_URL,
    capital: Annotated[float, typer.Option(help="Initial virtual capital in USD")] = 1000.0,
    bucket_seconds: Annotated[int, typer.Option(help="Seconds per snapshot bucket")] = 1,
    kelly_frac: Annotated[float, typer.Option(help="Fractional Kelly multiplier")] = 0.25,
    max_position_pct: Annotated[
        float, typer.Option(help="Max fraction of capital per market")
    ] = 0.1,
    max_slippage: Annotated[
        float | None,
        typer.Option(help="Max slippage tolerance (0-1 scale, e.g. 0.05). None disables."),
    ] = 0.05,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Enable per-trade logging")
    ] = False,
) -> None:
    """Grid-search late snipe parameters on 5-minute markets.

    Sweep threshold (0.55-0.95) and window (120s-10s) parameters,
    replay each combination against real tick data with order book
    liquidity checking, and display results as markdown tables.
    """
    if not start or not end:
        typer.echo("Error: --start and --end dates are required", err=True)
        raise typer.Exit(code=1)

    if verbose:
        configure_verbose_logging()

    start_ts = parse_date(start)
    end_ts = parse_date(end)
    if start_ts >= end_ts:
        typer.echo("Error: --start must be before --end", err=True)
        raise typer.Exit(code=1)

    start_ms = start_ts * _MS_PER_SECOND
    end_ms = end_ts * _MS_PER_SECOND

    thresholds = _build_threshold_grid()
    windows = _build_window_grid()
    total_combos = len(thresholds) * len(windows)

    typer.echo("Grid backtest: late snipe on 5-minute markets")
    typer.echo(f"Period: {start} to {end}")
    typer.echo(f"DB: {db_url}")
    typer.echo(f"Thresholds: {[float(t) for t in thresholds]}")
    typer.echo(f"Windows: {windows}, Combinations: {total_combos}")
    typer.echo(f"Capital: ${capital}, Bucket: {bucket_seconds}s, Kelly: {kelly_frac}")
    typer.echo("")

    typer.echo("Loading ticks from database...")
    all_ticks, book_data = asyncio.run(load_ticks(db_url, start_ms, end_ms))

    if not all_ticks:
        typer.echo("No ticks found in the specified date range.")
        return

    total_ticks = sum(len(t) for t in all_ticks.values())
    typer.echo(f"Found {len(all_ticks)} conditions with {total_ticks} ticks")
    if book_data:
        total_books = sum(len(b) for b in book_data.values())
        typer.echo(f"Found {total_books} order book snapshots for {len(book_data)} tokens")
    typer.echo("")

    slip = Decimal(str(max_slippage)) if max_slippage is not None else None
    result = run_grid_backtest(
        all_ticks,
        book_snapshots=book_data or None,
        thresholds=thresholds,
        windows=windows,
        capital=Decimal(str(capital)),
        kelly_frac=Decimal(str(kelly_frac)),
        max_position_pct=Decimal(str(max_position_pct)),
        bucket_seconds=bucket_seconds,
        window_minutes=_WINDOW_MINUTES,
        max_slippage=slip,
    )

    _display_results(result)
