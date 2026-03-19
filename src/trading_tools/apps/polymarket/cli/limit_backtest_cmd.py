"""CLI command for backtesting limit order spread capture.

Sweep bid prices, order sizes, and entry delays across a grid, replay
each combination against historical order book snapshots, and display
results as markdown tables showing fill rates, P&L, and Sharpe ratios.
"""

import asyncio
import os
from decimal import Decimal
from typing import Annotated

import typer

from trading_tools.apps.polymarket.backtest_common import (
    configure_verbose_logging,
    parse_date,
)
from trading_tools.apps.polymarket.cli.directional_backtest_cmd import (
    fetch_binance_candles,
)
from trading_tools.apps.spread_capture.limit_backtest import (
    format_limit_grid_table,
    run_limit_grid,
)
from trading_tools.apps.tick_collector.repository import TickRepository

_DEFAULT_DB_URL = os.environ.get("TICK_DB_URL", "sqlite+aiosqlite:///tick_data.db")


def _parse_decimal_list(value: str) -> list[Decimal]:
    """Parse a comma-separated string into a sorted list of Decimals.

    Args:
        value: Comma-separated numeric string (e.g. ``"0.10,0.20,0.30"``).

    Returns:
        Sorted list of Decimal values.

    """
    return sorted(Decimal(v.strip()) for v in value.split(","))


def limit_backtest(
    start: Annotated[str, typer.Option(help="Start date YYYY-MM-DD")] = "",
    end: Annotated[str, typer.Option(help="End date YYYY-MM-DD")] = "",
    db_url: Annotated[
        str, typer.Option(help="SQLAlchemy async DB URL for tick data")
    ] = _DEFAULT_DB_URL,
    bid_up_vals: Annotated[
        str,
        typer.Option("--bid-up", help="Comma-separated Up bid prices"),
    ] = "0.05,0.10,0.15,0.20,0.25,0.30",
    bid_down_vals: Annotated[
        str,
        typer.Option("--bid-down", help="Comma-separated Down bid prices"),
    ] = "0.05,0.10,0.15,0.20,0.25,0.30",
    order_size_vals: Annotated[
        str,
        typer.Option("--order-sizes", help="Comma-separated order sizes in tokens"),
    ] = "10,20,50",
    entry_delay_vals: Annotated[
        str,
        typer.Option(
            "--entry-delays",
            help="Comma-separated entry delay fractions (0.0 = at open)",
        ),
    ] = "0.0,0.10,0.20",
    series_slug: Annotated[
        str | None,
        typer.Option("--series-slug", help="Filter to a specific series slug"),
    ] = None,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Enable per-window logging")
    ] = False,
) -> None:
    """Backtest limit order spread capture on historical order books.

    Simulate placing resting limit buy orders on both Up and Down tokens
    across a grid of bid prices, order sizes, and entry delays.  Display
    fill rates, P&L, and Sharpe ratios as markdown tables.
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

    bid_prices_up = _parse_decimal_list(bid_up_vals)
    bid_prices_down = _parse_decimal_list(bid_down_vals)
    order_sizes = _parse_decimal_list(order_size_vals)
    entry_delays = _parse_decimal_list(entry_delay_vals)

    total_combos = len(bid_prices_up) * len(bid_prices_down) * len(order_sizes) * len(entry_delays)

    typer.echo("Limit Order Spread Capture Backtest")
    typer.echo(f"Period: {start} to {end}")
    typer.echo(f"DB: {db_url}")
    typer.echo(f"Bid Up prices: {[float(p) for p in bid_prices_up]}")
    typer.echo(f"Bid Down prices: {[float(p) for p in bid_prices_down]}")
    typer.echo(f"Order sizes: {[float(s) for s in order_sizes]}")
    typer.echo(f"Entry delays: {[float(d) for d in entry_delays]}")
    typer.echo(f"Combinations: {total_combos}")
    typer.echo("")

    async def _run() -> None:
        repo = TickRepository(db_url)
        try:
            # Discover assets and pre-fetch Binance candles for outcome resolution
            metadata_list = await repo.get_market_metadata_in_range(
                start_ts, end_ts, series_slug=series_slug
            )
            assets = sorted({m.asset for m in metadata_list})
            typer.echo(
                f"Found {len(metadata_list)} windows across "
                f"{len(assets)} assets: {', '.join(assets)}"
            )

            candles_by_asset = await fetch_binance_candles(assets, start_ts, end_ts, lookback=0)

            result = await run_limit_grid(
                repo=repo,
                start_ts=start_ts,
                end_ts=end_ts,
                bid_prices_up=bid_prices_up,
                bid_prices_down=bid_prices_down,
                order_sizes=order_sizes,
                entry_delays=entry_delays,
                series_slug=series_slug,
                candles_by_asset=candles_by_asset,
            )
        finally:
            await repo.close()

        typer.echo(f"Total windows: {result.total_windows}")
        typer.echo("")
        typer.echo("=== Fill Rate (Both Sides) ===")
        typer.echo(format_limit_grid_table(result, metric="fill_rate_both"))
        typer.echo("")
        typer.echo("=== Total P&L ===")
        typer.echo(format_limit_grid_table(result, metric="total_pnl"))
        typer.echo("")
        typer.echo("=== Sharpe Ratio ===")
        typer.echo(format_limit_grid_table(result, metric="sharpe"))
        typer.echo("")
        typer.echo("=== Avg Guaranteed P&L (Both-Filled Windows) ===")
        typer.echo(format_limit_grid_table(result, metric="avg_guaranteed_pnl"))

    asyncio.run(_run())
