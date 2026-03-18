"""CLI command for grid-searching spread capture strategy parameters.

Sweep hedge thresholds and signal delay across a grid, replay each
combination against historical data, and display results as markdown
tables.
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
from trading_tools.apps.spread_capture.config import SpreadCaptureConfig
from trading_tools.apps.spread_capture.grid_backtest import (
    format_spread_grid_table,
    run_spread_grid,
)
from trading_tools.apps.tick_collector.repository import TickRepository

_DEFAULT_DB_URL = os.environ.get("TICK_DB_URL", "sqlite+aiosqlite:///tick_data.db")


def _parse_decimal_list(value: str) -> list[Decimal]:
    """Parse a comma-separated string into a list of Decimals.

    Args:
        value: Comma-separated numeric string (e.g. ``"0.40,0.45,0.50"``).

    Returns:
        Sorted list of Decimal values.

    """
    return sorted(Decimal(v.strip()) for v in value.split(","))


def _parse_int_list(value: str) -> list[int]:
    """Parse a comma-separated string into a list of ints.

    Args:
        value: Comma-separated integer string (e.g. ``"180,300,420"``).

    Returns:
        Sorted list of integer values.

    """
    return sorted(int(v.strip()) for v in value.split(","))


def grid_spread(
    start: Annotated[str, typer.Option(help="Start date YYYY-MM-DD")] = "",
    end: Annotated[str, typer.Option(help="End date YYYY-MM-DD")] = "",
    db_url: Annotated[
        str, typer.Option(help="SQLAlchemy async DB URL for tick data")
    ] = _DEFAULT_DB_URL,
    hedge_start_vals: Annotated[
        str, typer.Option("--hedge-start", help="Comma-separated hedge start values")
    ] = "0.35,0.40,0.45,0.50",
    hedge_end_vals: Annotated[
        str, typer.Option("--hedge-end", help="Comma-separated hedge end values")
    ] = "0.55,0.60,0.65,0.70,0.80,0.90",
    signal_delay_vals: Annotated[
        str, typer.Option("--signal-delay", help="Comma-separated signal delay values (seconds)")
    ] = "180,300,420",
    series_slug: Annotated[
        str | None, typer.Option("--series-slug", help="Filter to a specific series slug")
    ] = None,
    capital: Annotated[float, typer.Option(help="Initial virtual capital in USD")] = 1000.0,
    poll_interval: Annotated[
        int, typer.Option(help="Seconds between poll cycles during replay")
    ] = 5,
    slippage: Annotated[float, typer.Option(help="Paper slippage percentage (e.g. 0.005)")] = 0.005,
    verbose: Annotated[  # noqa: FBT002
        bool, typer.Option("--verbose", "-v", help="Enable per-window logging")
    ] = False,
) -> None:
    """Grid-search spread capture parameters on historical data.

    Sweep hedge start/end thresholds and signal delay, replay each
    combination against stored market data, and display results as
    markdown tables.
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

    hedge_starts = _parse_decimal_list(hedge_start_vals)
    hedge_ends = _parse_decimal_list(hedge_end_vals)
    signal_delays = _parse_int_list(signal_delay_vals)

    base_config = SpreadCaptureConfig(
        strategy="accumulate",
        capital=Decimal(str(capital)),
        poll_interval=poll_interval,
        paper_slippage_pct=Decimal(str(slippage)),
    )

    total_combos = len(hedge_starts) * len(hedge_ends) * len(signal_delays)

    typer.echo("Spread Capture Grid Search")
    typer.echo(f"Period: {start} to {end}")
    typer.echo(f"DB: {db_url}")
    typer.echo(f"Hedge starts: {[float(h) for h in hedge_starts]}")
    typer.echo(f"Hedge ends: {[float(h) for h in hedge_ends]}")
    typer.echo(f"Signal delays: {signal_delays}")
    typer.echo(f"Combinations: {total_combos}")
    typer.echo(f"Capital: ${capital}")
    typer.echo("")

    async def _run() -> None:
        repo = TickRepository(db_url)
        try:
            result = await run_spread_grid(
                base_config=base_config,
                repo=repo,
                start_ts=start_ts,
                end_ts=end_ts,
                hedge_starts=hedge_starts,
                hedge_ends=hedge_ends,
                signal_delays=signal_delays,
                series_slug=series_slug,
            )
        finally:
            await repo.close()

        typer.echo("=== Return % ===")
        typer.echo(format_spread_grid_table(result, metric="return_pct"))
        typer.echo("")
        typer.echo("=== Total Trades ===")
        typer.echo(format_spread_grid_table(result, metric="total_trades"))
        typer.echo("")
        typer.echo("=== Win Rate ===")
        typer.echo(format_spread_grid_table(result, metric="win_rate"))

    asyncio.run(_run())
