"""CLI command for backtesting the directional trading algorithm.

Replay historical market windows through the directional engine
using stored market metadata and Binance candle data.  Report
aggregate performance and calibration metrics.
"""

from __future__ import annotations

import asyncio
import os
from decimal import Decimal
from typing import Annotated

import typer

from trading_tools.apps.directional.backtest_runner import run_directional_backtest
from trading_tools.apps.directional.config import DirectionalConfig
from trading_tools.apps.polymarket.backtest_common import (
    configure_verbose_logging,
    parse_date,
)
from trading_tools.apps.tick_collector.repository import TickRepository

_DEFAULT_DB_URL = os.environ.get("TICK_DB_URL", "sqlite+aiosqlite:///tick_data.db")


def directional_backtest(
    start: Annotated[str, typer.Option(help="Start date YYYY-MM-DD")] = "",
    end: Annotated[str, typer.Option(help="End date YYYY-MM-DD")] = "",
    db_url: Annotated[
        str, typer.Option(help="SQLAlchemy async DB URL for tick data")
    ] = _DEFAULT_DB_URL,
    series_slug: Annotated[
        str | None, typer.Option("--series-slug", help="Filter to a specific series slug")
    ] = None,
    capital: Annotated[float, typer.Option(help="Initial virtual capital in USD")] = 1000.0,
    min_edge: Annotated[float, typer.Option(help="Minimum probability edge to enter")] = 0.05,
    kelly_fraction: Annotated[
        float, typer.Option(help="Fractional Kelly multiplier (e.g. 0.5)")
    ] = 0.5,
    entry_start: Annotated[int, typer.Option(help="Seconds before close to start entries")] = 30,
    entry_end: Annotated[int, typer.Option(help="Seconds before close to stop entries")] = 10,
    signal_lookback: Annotated[int, typer.Option(help="Seconds of Binance candle lookback")] = 300,
    poll_interval: Annotated[
        int, typer.Option(help="Seconds between poll cycles during replay")
    ] = 3,
    verbose: Annotated[  # noqa: FBT002
        bool, typer.Option("--verbose", "-v", help="Enable per-window logging")
    ] = False,
) -> None:
    """Backtest the directional trading algorithm on historical data.

    Replay market windows from stored metadata and compute directional
    P&L with probability calibration metrics (Brier score).  Requires
    a tick database with ``market_metadata`` tables populated by the
    tick collector.
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

    config = DirectionalConfig(
        capital=Decimal(str(capital)),
        min_edge=Decimal(str(min_edge)),
        kelly_fraction=Decimal(str(kelly_fraction)),
        entry_window_start=entry_start,
        entry_window_end=entry_end,
        signal_lookback_seconds=signal_lookback,
        poll_interval=poll_interval,
    )

    typer.echo("Directional Backtest")
    typer.echo(f"Period: {start} to {end}")
    typer.echo(f"DB: {db_url}")
    typer.echo(f"Capital: ${capital}  Min edge: {min_edge}  Kelly: {kelly_fraction}")
    typer.echo(f"Entry window: [{entry_end}s, {entry_start}s] before close")
    typer.echo(f"Signal lookback: {signal_lookback}s")
    typer.echo("")

    async def _run() -> None:
        repo = TickRepository(db_url)
        try:
            result = await run_directional_backtest(
                config=config,
                repo=repo,
                start_ts=start_ts,
                end_ts=end_ts,
                series_slug=series_slug,
            )
        finally:
            await repo.close()

        typer.echo("--- Directional Backtest Results ---")
        typer.echo(f"Windows replayed: {result.total_windows}")
        typer.echo(f"Trades entered:   {result.total_trades}")
        typer.echo(f"Skipped:          {result.skipped}")
        typer.echo(f"Initial capital:  ${result.initial_capital:.2f}")
        typer.echo(f"Final capital:    ${result.final_capital:.2f}")
        typer.echo(f"P&L: ${result.total_pnl:.2f} ({result.return_pct:.2f}%)")
        typer.echo(f"Wins: {result.wins}  Losses: {result.losses}")
        if result.win_rate > 0:
            typer.echo(f"Win rate: {result.win_rate * Decimal(100):.1f}%")
        typer.echo(f"Avg P&L per trade: ${result.avg_pnl:.4f}")
        typer.echo("")
        typer.echo("--- Calibration ---")
        typer.echo(f"Brier score:           {result.brier_score:.4f}")
        typer.echo(f"Avg P(win) when correct:   {result.avg_p_when_correct:.4f}")
        typer.echo(f"Avg P(win) when incorrect: {result.avg_p_when_incorrect:.4f}")

    asyncio.run(_run())
