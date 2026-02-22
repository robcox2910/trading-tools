"""CLI command for running the Polymarket paper trading bot.

Launch the async polling engine with a configurable strategy, capital,
and market selection. Display a summary of results when the bot stops.
"""

import asyncio
import logging
from decimal import Decimal
from typing import Annotated

import typer

from trading_tools.apps.polymarket_bot.engine import PaperTradingEngine
from trading_tools.apps.polymarket_bot.models import BotConfig
from trading_tools.apps.polymarket_bot.strategy_factory import (
    PM_STRATEGY_NAMES,
    build_pm_strategy,
)
from trading_tools.clients.polymarket.client import PolymarketClient
from trading_tools.clients.polymarket.exceptions import PolymarketAPIError


def _configure_verbose_logging() -> None:
    """Enable INFO-level logging for tick-by-tick engine output."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")


def bot(  # noqa: PLR0913
    strategy: Annotated[
        str, typer.Option(help=f"Strategy name: {', '.join(PM_STRATEGY_NAMES)}")
    ] = "pm_mean_reversion",
    markets: Annotated[str, typer.Option(help="Comma-separated condition IDs to track")] = "",
    capital: Annotated[float, typer.Option(help="Initial virtual capital in USD")] = 1000.0,
    poll_interval: Annotated[int, typer.Option(help="Seconds between market data polls")] = 30,
    max_ticks: Annotated[
        int | None, typer.Option(help="Stop after N ticks (None = unlimited)")
    ] = None,
    max_position_pct: Annotated[
        float, typer.Option(help="Max fraction of capital per market")
    ] = 0.1,
    kelly_frac: Annotated[float, typer.Option(help="Fractional Kelly multiplier")] = 0.25,
    period: Annotated[int, typer.Option(help="Rolling window period (mean reversion)")] = 20,
    z_threshold: Annotated[float, typer.Option(help="Z-score threshold (mean reversion)")] = 1.5,
    spread_pct: Annotated[float, typer.Option(help="Half-spread fraction (market making)")] = 0.03,
    imbalance_threshold: Annotated[
        float, typer.Option(help="Imbalance threshold (liquidity)")
    ] = 0.65,
    min_edge: Annotated[float, typer.Option(help="Minimum edge (cross-market arb)")] = 0.02,
    verbose: Annotated[  # noqa: FBT002
        bool, typer.Option("--verbose", "-v", help="Enable tick-by-tick logging")
    ] = False,
) -> None:
    """Run the Polymarket paper trading bot.

    Poll prediction markets at a configurable interval, feed snapshots to
    a strategy, size positions with Kelly criterion, and track virtual P&L.
    """
    if verbose:
        _configure_verbose_logging()

    asyncio.run(
        _bot(
            strategy=strategy,
            markets=markets,
            capital=capital,
            poll_interval=poll_interval,
            max_ticks=max_ticks,
            max_position_pct=max_position_pct,
            kelly_frac=kelly_frac,
            period=period,
            z_threshold=z_threshold,
            spread_pct=spread_pct,
            imbalance_threshold=imbalance_threshold,
            min_edge=min_edge,
        )
    )


async def _bot(  # noqa: PLR0913
    *,
    strategy: str,
    markets: str,
    capital: float,
    poll_interval: int,
    max_ticks: int | None,
    max_position_pct: float,
    kelly_frac: float,
    period: int,
    z_threshold: float,
    spread_pct: float,
    imbalance_threshold: float,
    min_edge: float,
) -> None:
    """Run the paper trading bot asynchronously.

    Args:
        strategy: Strategy name to use.
        markets: Comma-separated condition IDs.
        capital: Initial virtual capital.
        poll_interval: Seconds between polls.
        max_ticks: Maximum number of ticks.
        max_position_pct: Maximum position size as fraction of capital.
        kelly_frac: Fractional Kelly multiplier.
        period: Rolling window period for mean reversion.
        z_threshold: Z-score threshold for mean reversion.
        spread_pct: Half-spread for market making.
        imbalance_threshold: Threshold for liquidity imbalance.
        min_edge: Minimum edge for cross-market arb.

    """
    market_ids = tuple(m.strip() for m in markets.split(",") if m.strip())
    if not market_ids:
        typer.echo("Error: --markets must specify at least one condition ID", err=True)
        raise typer.Exit(code=1)

    try:
        pm_strategy = build_pm_strategy(
            strategy,
            period=period,
            z_threshold=z_threshold,
            spread_pct=spread_pct,
            imbalance_threshold=imbalance_threshold,
            min_edge=min_edge,
        )
    except typer.BadParameter as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    config = BotConfig(
        poll_interval_seconds=poll_interval,
        initial_capital=Decimal(str(capital)),
        max_position_pct=Decimal(str(max_position_pct)),
        kelly_fraction=Decimal(str(kelly_frac)),
        markets=market_ids,
    )

    typer.echo(f"Starting paper trading bot: {pm_strategy.name}")
    typer.echo(f"Markets: {', '.join(market_ids)}")
    typer.echo(f"Capital: ${config.initial_capital}")
    typer.echo(f"Poll interval: {config.poll_interval_seconds}s")
    if max_ticks is not None:
        typer.echo(f"Max ticks: {max_ticks}")
    typer.echo("")

    try:
        async with PolymarketClient() as client:
            engine = PaperTradingEngine(client, pm_strategy, config)
            result = await engine.run(max_ticks=max_ticks)
    except PolymarketAPIError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo("\n--- Paper Trading Results ---")
    typer.echo(f"Strategy: {result.strategy_name}")
    typer.echo(f"Snapshots processed: {result.snapshots_processed}")
    typer.echo(f"Initial capital: ${result.initial_capital:.2f}")
    typer.echo(f"Final capital:   ${result.final_capital:.2f}")
    typer.echo(f"Total trades: {len(result.trades)}")

    if result.metrics:
        typer.echo("\nMetrics:")
        for key, value in result.metrics.items():
            typer.echo(f"  {key}: {value:.4f}")
