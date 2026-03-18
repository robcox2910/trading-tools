"""CLI command for backtesting the spread capture strategy.

Replay historical market windows through the spread capture engine
using stored order book snapshots and market metadata.  Report
aggregate performance metrics.
"""

from __future__ import annotations

import asyncio
import os
from decimal import Decimal
from typing import Annotated

import typer

from trading_tools.apps.polymarket.backtest_common import (
    configure_verbose_logging,
    parse_date,
)
from trading_tools.apps.spread_capture.backtest_runner import run_spread_backtest
from trading_tools.apps.spread_capture.config import SpreadCaptureConfig
from trading_tools.apps.tick_collector.repository import TickRepository

_MS_PER_SECOND = 1000
_DEFAULT_DB_URL = os.environ.get("TICK_DB_URL", "sqlite+aiosqlite:///tick_data.db")


def backtest_spread(
    start: Annotated[str, typer.Option(help="Start date YYYY-MM-DD")] = "",
    end: Annotated[str, typer.Option(help="End date YYYY-MM-DD")] = "",
    db_url: Annotated[
        str, typer.Option(help="SQLAlchemy async DB URL for tick data")
    ] = _DEFAULT_DB_URL,
    strategy: Annotated[str, typer.Option(help="Strategy: 'accumulate' (default)")] = "accumulate",
    series_slug: Annotated[
        str | None, typer.Option("--series-slug", help="Filter to a specific series slug")
    ] = None,
    capital: Annotated[float, typer.Option(help="Initial virtual capital in USD")] = 1000.0,
    signal_delay: Annotated[
        int, typer.Option(help="Seconds of Binance lookback before window")
    ] = 300,
    hedge_start: Annotated[float, typer.Option(help="Early hedge threshold (e.g. 0.45)")] = 0.45,
    hedge_end: Annotated[float, typer.Option(help="Late hedge threshold (e.g. 0.65)")] = 0.65,
    hedge_start_pct: Annotated[
        float, typer.Option(help="Start hedging at this fraction of window")
    ] = 0.20,
    max_fill_age_pct: Annotated[
        float, typer.Option(help="Stop fills after this fraction of window")
    ] = 0.80,
    max_imbalance: Annotated[float, typer.Option(help="Max qty ratio between legs")] = 3.0,
    fill_size: Annotated[float, typer.Option(help="Tokens per adjustment fill")] = 2.0,
    initial_fill: Annotated[float, typer.Option(help="Tokens for first fill on each side")] = 20.0,
    poll_interval: Annotated[
        int, typer.Option(help="Seconds between poll cycles during replay")
    ] = 5,
    slippage: Annotated[float, typer.Option(help="Paper slippage percentage (e.g. 0.005)")] = 0.005,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Enable per-window logging")
    ] = False,
) -> None:
    """Backtest the spread capture strategy on historical data.

    Replay market windows from stored metadata and order book snapshots.
    Requires a tick database with ``market_metadata`` and
    ``order_book_snapshots`` tables populated by the tick collector.
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

    config = SpreadCaptureConfig(
        strategy=strategy,
        capital=Decimal(str(capital)),
        signal_delay_seconds=signal_delay,
        hedge_start_threshold=Decimal(str(hedge_start)),
        hedge_end_threshold=Decimal(str(hedge_end)),
        hedge_start_pct=Decimal(str(hedge_start_pct)),
        max_fill_age_pct=Decimal(str(max_fill_age_pct)),
        max_imbalance_ratio=Decimal(str(max_imbalance)),
        fill_size_tokens=Decimal(str(fill_size)),
        initial_fill_size=Decimal(str(initial_fill)),
        poll_interval=poll_interval,
        paper_slippage_pct=Decimal(str(slippage)),
    )

    typer.echo("Spread Capture Backtest")
    typer.echo(f"Period: {start} to {end}")
    typer.echo(f"DB: {db_url}")
    typer.echo(f"Capital: ${capital}  Signal delay: {signal_delay}s")
    typer.echo(f"Hedge: {hedge_start}→{hedge_end}  Imbalance: {max_imbalance}")
    typer.echo(f"Fill size: {fill_size}  Initial fill: {initial_fill}")
    typer.echo("")

    async def _run() -> None:
        repo = TickRepository(db_url)
        try:
            result = await run_spread_backtest(
                config=config,
                repo=repo,
                start_ts=start_ts,
                end_ts=end_ts,
                series_slug=series_slug,
            )
        finally:
            await repo.close()

        typer.echo("--- Backtest Results ---")
        typer.echo(f"Windows replayed: {result.total_windows}")
        typer.echo(f"Total trades: {result.total_trades}")
        typer.echo(f"Initial capital: ${result.initial_capital:.2f}")
        typer.echo(f"Final capital:   ${result.final_capital:.2f}")
        typer.echo(f"P&L: ${result.total_pnl:.2f} ({result.return_pct:.2f}%)")
        typer.echo(f"Wins: {result.wins}  Losses: {result.losses}")
        if result.win_rate > 0:
            typer.echo(f"Win rate: {result.win_rate * Decimal(100):.1f}%")
        typer.echo(f"Avg P&L per trade: ${result.avg_pnl:.4f}")

    asyncio.run(_run())
