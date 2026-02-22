"""CLI command for running the Polymarket paper trading bot.

Launch the async polling engine with a configurable strategy, capital,
and market selection. Support auto-discovery of 5-minute crypto markets
via Gamma API series slugs. Display a summary of results when the bot stops.
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

_CRYPTO_5M_SERIES = (
    "btc-updown-5m",
    "eth-updown-5m",
    "sol-updown-5m",
    "xrp-updown-5m",
)


def _configure_verbose_logging() -> None:
    """Enable INFO-level logging for tick-by-tick engine output."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")


def bot(  # noqa: PLR0913
    strategy: Annotated[
        str, typer.Option(help=f"Strategy name: {', '.join(PM_STRATEGY_NAMES)}")
    ] = "pm_mean_reversion",
    markets: Annotated[str, typer.Option(help="Comma-separated condition IDs to track")] = "",
    series: Annotated[
        str,
        typer.Option(help="Comma-separated series slugs for auto-discovery (e.g. btc-updown-5m)"),
    ] = "",
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
    snipe_threshold: Annotated[
        float, typer.Option(help="Price threshold for late snipe (0.5-1.0)")
    ] = 0.9,
    snipe_window: Annotated[
        int, typer.Option(help="Seconds before market end to start sniping")
    ] = 60,
    verbose: Annotated[  # noqa: FBT002
        bool, typer.Option("--verbose", "-v", help="Enable tick-by-tick logging")
    ] = False,
) -> None:
    """Run the Polymarket paper trading bot.

    Poll prediction markets at a configurable interval, feed snapshots to
    a strategy, size positions with Kelly criterion, and track virtual P&L.

    Use ``--series`` to auto-discover active 5-minute crypto markets, or
    ``--markets`` to specify condition IDs directly. Both can be combined.
    """
    if verbose:
        _configure_verbose_logging()

    asyncio.run(
        _bot(
            strategy=strategy,
            markets=markets,
            series=series,
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
            snipe_threshold=snipe_threshold,
            snipe_window=snipe_window,
        )
    )


async def _discover_markets(
    client: PolymarketClient,
    series: str,
) -> list[tuple[str, str]]:
    """Discover active markets from series slugs.

    Parse the comma-separated series string, expanding the special value
    ``"crypto-5m"`` into all four crypto Up/Down 5-minute series.

    Args:
        client: Polymarket API client for Gamma lookups.
        series: Comma-separated series slugs or ``"crypto-5m"`` shortcut.

    Returns:
        List of ``(condition_id, end_date)`` tuples.

    """
    slugs: list[str] = []
    for s in series.split(","):
        s = s.strip()  # noqa: PLW2901
        if not s:
            continue
        if s == "crypto-5m":
            slugs.extend(_CRYPTO_5M_SERIES)
        else:
            slugs.append(s)

    if not slugs:
        return []

    typer.echo(f"Discovering markets for series: {', '.join(slugs)}...")
    discovered = await client.discover_series_markets(slugs)
    for cid, end_date in discovered:
        typer.echo(f"  Found: {cid[:20]}... ends {end_date}")
    return discovered


async def _bot(  # noqa: PLR0913
    *,
    strategy: str,
    markets: str,
    series: str,
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
    snipe_threshold: float,
    snipe_window: int,
) -> None:
    """Run the paper trading bot asynchronously.

    Args:
        strategy: Strategy name to use.
        markets: Comma-separated condition IDs.
        series: Comma-separated series slugs for auto-discovery.
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
        snipe_threshold: Price threshold for late snipe strategy.
        snipe_window: Window in seconds before market end for sniping.

    """
    market_ids = tuple(m.strip() for m in markets.split(",") if m.strip())
    market_end_times: tuple[tuple[str, str], ...] = ()

    # Discover markets from series slugs
    if series.strip():
        try:
            async with PolymarketClient() as client:
                discovered = await _discover_markets(client, series)
        except PolymarketAPIError as exc:
            typer.echo(f"Warning: Series discovery failed: {exc}", err=True)
            discovered = []

        if discovered:
            discovered_ids = tuple(cid for cid, _ in discovered)
            market_end_times = tuple(discovered)
            market_ids = (*market_ids, *discovered_ids)

    if not market_ids:
        typer.echo(
            "Error: specify --markets or --series (e.g. --series crypto-5m)",
            err=True,
        )
        raise typer.Exit(code=1)

    try:
        pm_strategy = build_pm_strategy(
            strategy,
            period=period,
            z_threshold=z_threshold,
            spread_pct=spread_pct,
            imbalance_threshold=imbalance_threshold,
            min_edge=min_edge,
            snipe_threshold=snipe_threshold,
            snipe_window=snipe_window,
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
        market_end_times=market_end_times,
    )

    typer.echo(f"Starting paper trading bot: {pm_strategy.name}")
    typer.echo(f"Markets: {len(market_ids)} tracked")
    for mid in market_ids:
        typer.echo(f"  {mid[:40]}...")
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
