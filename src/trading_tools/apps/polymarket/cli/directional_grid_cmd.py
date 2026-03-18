"""CLI command for grid-searching directional trading parameters.

Sweep estimator weights, entry timing, and sizing parameters across
a grid of combinations.  Each combination is backtested against
historical data and ranked by Brier score (calibration quality).
"""

from __future__ import annotations

import asyncio
import os
from decimal import Decimal
from typing import Annotated

import typer

from trading_tools.apps.directional.config import DirectionalConfig
from trading_tools.apps.directional.grid_backtest import (
    format_grid_table,
    run_directional_grid,
)
from trading_tools.apps.polymarket.backtest_common import (
    configure_verbose_logging,
    parse_date,
)
from trading_tools.apps.polymarket.cli.directional_backtest_cmd import fetch_binance_candles
from trading_tools.apps.tick_collector.repository import TickRepository

_DEFAULT_DB_URL = os.environ.get("TICK_DB_URL", "sqlite+aiosqlite:///tick_data.db")


def _parse_decimal_list(value: str) -> list[Decimal]:
    """Parse a comma-separated string into a list of Decimals.

    Args:
        value: Comma-separated decimal values (e.g. ``"0.3,0.4,0.5"``).

    Returns:
        List of Decimal values.

    """
    return [Decimal(v.strip()) for v in value.split(",") if v.strip()]


def _parse_int_list(value: str) -> list[int]:
    """Parse a comma-separated string into a list of integers.

    Args:
        value: Comma-separated integer values (e.g. ``"60,90,120"``).

    Returns:
        List of integer values.

    """
    return [int(v.strip()) for v in value.split(",") if v.strip()]


def _build_grid(
    w_whale_vals: str,
    w_momentum_vals: str,
    min_edge_vals: str,
    entry_start_vals: str,
    *,
    capital: float,
    signal_lookback: int,
) -> tuple[dict[str, list[object]], DirectionalConfig]:
    """Build the parameter grid and base config from CLI value strings.

    Args:
        w_whale_vals: Comma-separated whale weight values.
        w_momentum_vals: Comma-separated momentum weight values.
        min_edge_vals: Comma-separated min_edge values.
        entry_start_vals: Comma-separated entry window start values.
        capital: Initial capital.
        signal_lookback: Binance candle lookback seconds.

    Returns:
        Tuple of (param_grid, base_config).

    """
    param_grid: dict[str, list[object]] = {}
    w_whale_list = _parse_decimal_list(w_whale_vals)
    if len(w_whale_list) > 1:
        param_grid["w_whale"] = list(w_whale_list)

    w_mom_list = _parse_decimal_list(w_momentum_vals)
    if len(w_mom_list) > 1:
        param_grid["w_momentum"] = list(w_mom_list)

    min_edge_list = _parse_decimal_list(min_edge_vals)
    if len(min_edge_list) > 1:
        param_grid["min_edge"] = list(min_edge_list)

    entry_start_list = _parse_int_list(entry_start_vals)
    if len(entry_start_list) > 1:
        param_grid["entry_window_start"] = list(entry_start_list)

    base_config = DirectionalConfig(
        capital=Decimal(str(capital)),
        signal_lookback_seconds=signal_lookback,
        w_whale=w_whale_list[0] if len(w_whale_list) == 1 else Decimal("0.50"),
        w_momentum=w_mom_list[0] if len(w_mom_list) == 1 else Decimal("0.15"),
        min_edge=min_edge_list[0] if len(min_edge_list) == 1 else Decimal("0.05"),
        entry_window_start=entry_start_list[0] if len(entry_start_list) == 1 else 120,
    )
    return param_grid, base_config


def directional_grid(
    start: Annotated[str, typer.Option(help="Start date YYYY-MM-DD")] = "",
    end: Annotated[str, typer.Option(help="End date YYYY-MM-DD")] = "",
    db_url: Annotated[
        str, typer.Option(help="SQLAlchemy async DB URL for tick data")
    ] = _DEFAULT_DB_URL,
    series_slug: Annotated[
        str | None, typer.Option("--series-slug", help="Filter to a specific series slug")
    ] = None,
    capital: Annotated[float, typer.Option(help="Initial virtual capital in USD")] = 1000.0,
    w_whale_vals: Annotated[
        str, typer.Option(help="Comma-separated whale weight values to sweep")
    ] = "0.30,0.40,0.50,0.60",
    w_momentum_vals: Annotated[
        str, typer.Option(help="Comma-separated momentum weight values")
    ] = "0.10,0.15,0.20",
    min_edge_vals: Annotated[
        str, typer.Option(help="Comma-separated min_edge values")
    ] = "0.03,0.05,0.07",
    entry_start_vals: Annotated[
        str, typer.Option(help="Comma-separated entry_window_start values (seconds)")
    ] = "60,120,180",
    signal_lookback: Annotated[int, typer.Option(help="Seconds of Binance candle lookback")] = 1200,
    top_n: Annotated[int, typer.Option(help="Show top N results")] = 20,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Enable per-window logging")
    ] = False,
) -> None:
    """Grid search directional trading parameters on historical data.

    Sweep estimator weights, entry timing, and min_edge across a grid.
    Each combination is backtested and ranked by Brier score (lower =
    better calibrated probability estimates).
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

    param_grid, base_config = _build_grid(
        w_whale_vals,
        w_momentum_vals,
        min_edge_vals,
        entry_start_vals,
        capital=capital,
        signal_lookback=signal_lookback,
    )

    if not param_grid:
        typer.echo("Error: at least one parameter must have multiple values to sweep", err=True)
        raise typer.Exit(code=1)

    total_combos = 1
    for vals in param_grid.values():
        total_combos *= len(vals)

    typer.echo("Directional Grid Search")
    typer.echo(f"Period: {start} to {end}")
    typer.echo(f"Capital: ${capital}")
    typer.echo(f"Combinations: {total_combos}")
    for name, vals in param_grid.items():
        typer.echo(f"  {name}: {vals}")
    typer.echo("")

    async def _run() -> None:
        repo = TickRepository(db_url)
        try:
            # Discover assets and pre-fetch candles
            metadata_list = await repo.get_market_metadata_in_range(
                start_ts, end_ts, series_slug=series_slug
            )
            assets = sorted({m.asset for m in metadata_list})
            typer.echo(
                f"Found {len(metadata_list)} windows across "
                f"{len(assets)} assets: {', '.join(assets)}"
            )

            candles_by_asset = await fetch_binance_candles(
                assets, start_ts, end_ts, signal_lookback
            )

            result = await run_directional_grid(
                base_config=base_config,
                repo=repo,
                start_ts=start_ts,
                end_ts=end_ts,
                param_grid=param_grid,
                candles_by_asset=candles_by_asset,
                series_slug=series_slug,
            )
        finally:
            await repo.close()

        typer.echo("")
        typer.echo(format_grid_table(result, top_n=top_n))

        if result.cells:
            best = result.cells[0]
            typer.echo("")
            typer.echo("--- Best Configuration ---")
            for k, v in best.params.items():
                typer.echo(f"  {k}: {v}")
            typer.echo(f"  Brier: {best.brier_score:.4f}")
            typer.echo(f"  Win rate: {best.win_rate * Decimal(100):.0f}%")
            typer.echo(f"  Return: {best.return_pct:.1f}%")

    asyncio.run(_run())
