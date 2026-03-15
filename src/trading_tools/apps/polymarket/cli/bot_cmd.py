"""CLI command for running the Polymarket paper trading bot.

Launch the WebSocket-driven engine with a configurable strategy, capital,
and market selection. Support auto-discovery of 5-minute crypto markets
via Gamma API series slugs. Display a summary of results when the bot stops.
"""

import asyncio
from decimal import Decimal
from typing import Annotated

import typer

from trading_tools.apps.polymarket.cli._helpers import (
    StrategyParams,
    configure_logging,
    resolve_market_ids,
)
from trading_tools.apps.polymarket_bot.engine import PaperTradingEngine
from trading_tools.apps.polymarket_bot.models import BotConfig
from trading_tools.apps.polymarket_bot.strategy_factory import PM_STRATEGY_NAMES
from trading_tools.apps.tick_collector.ws_client import MarketFeed
from trading_tools.clients.polymarket.client import PolymarketClient
from trading_tools.clients.polymarket.exceptions import PolymarketAPIError


def bot(
    strategy: Annotated[
        str, typer.Option(help=f"Strategy name: {', '.join(PM_STRATEGY_NAMES)}")
    ] = "pm_mean_reversion",
    markets: Annotated[str, typer.Option(help="Comma-separated condition IDs to track")] = "",
    series: Annotated[
        str,
        typer.Option(help="Comma-separated series slugs for auto-discovery (e.g. btc-updown-5m)"),
    ] = "",
    capital: Annotated[float, typer.Option(help="Initial virtual capital in USD")] = 1000.0,
    ob_refresh: Annotated[int, typer.Option(help="Seconds between order book refreshes")] = 30,
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
    ] = 0.8,
    snipe_window: Annotated[
        int, typer.Option(help="Seconds before market end to start sniping")
    ] = 60,
    fee_rate: Annotated[
        float, typer.Option(help="Fee rate parameter (0.25=crypto, 0.0175=sports, 0=disabled)")
    ] = 0.25,
    fee_exponent: Annotated[int, typer.Option(help="Fee exponent (2=crypto, 1=sports)")] = 2,
    max_loss_pct: Annotated[
        float, typer.Option(help="Stop bot at this drawdown %% (e.g. -20)")
    ] = -100.0,
    verbose: Annotated[  # noqa: FBT002
        bool, typer.Option("--verbose", "-v", help="Enable tick-by-tick logging")
    ] = False,
) -> None:
    """Run the Polymarket paper trading bot.

    Stream real-time trade prices via WebSocket and refresh order books
    periodically via HTTP. Feed snapshots to a strategy, size positions
    with Kelly criterion, and track virtual P&L.

    Use ``--series`` to auto-discover active 5-minute crypto markets, or
    ``--markets`` to specify condition IDs directly. Both can be combined.
    """
    configure_logging(verbose=verbose)

    strategy_params = StrategyParams(
        period=period,
        z_threshold=z_threshold,
        spread_pct=spread_pct,
        imbalance_threshold=imbalance_threshold,
        min_edge=min_edge,
        snipe_threshold=snipe_threshold,
        snipe_window=snipe_window,
    )

    asyncio.run(
        _bot(
            strategy=strategy,
            markets=markets,
            series=series,
            capital=capital,
            ob_refresh=ob_refresh,
            max_ticks=max_ticks,
            max_position_pct=max_position_pct,
            kelly_frac=kelly_frac,
            strategy_params=strategy_params,
            fee_rate=fee_rate,
            fee_exponent=fee_exponent,
            max_loss_pct=max_loss_pct,
        )
    )


async def _bot(
    *,
    strategy: str,
    markets: str,
    series: str,
    capital: float,
    ob_refresh: int,
    max_ticks: int | None,
    max_position_pct: float,
    kelly_frac: float,
    strategy_params: StrategyParams,
    fee_rate: float,
    fee_exponent: int,
    max_loss_pct: float,
) -> None:
    """Run the paper trading bot asynchronously.

    Args:
        strategy: Strategy name to use.
        markets: Comma-separated condition IDs.
        series: Comma-separated series slugs for auto-discovery.
        capital: Initial virtual capital.
        ob_refresh: Seconds between order book refreshes.
        max_ticks: Maximum number of ticks.
        max_position_pct: Maximum position size as fraction of capital.
        kelly_frac: Fractional Kelly multiplier.
        strategy_params: Shared strategy configuration parameters.
        fee_rate: Fee rate parameter in the polynomial formula.
        fee_exponent: Exponent in the polynomial fee formula.
        max_loss_pct: Stop bot at this drawdown percentage (e.g. -20).

    """
    async with PolymarketClient() as client:
        market_ids, market_end_times, series_slugs = await resolve_market_ids(
            client, markets, series
        )

    pm_strategy = strategy_params.build_strategy(strategy)

    config = BotConfig(
        order_book_refresh_seconds=ob_refresh,
        snipe_window_seconds=strategy_params.snipe_window,
        initial_capital=Decimal(str(capital)),
        max_position_pct=Decimal(str(max_position_pct)),
        kelly_fraction=Decimal(str(kelly_frac)),
        fee_rate=Decimal(str(fee_rate)),
        fee_exponent=fee_exponent,
        max_loss_pct=Decimal(str(max_loss_pct)),
        markets=market_ids,
        market_end_times=market_end_times,
        series_slugs=series_slugs,
    )

    typer.echo(f"Starting paper trading bot: {pm_strategy.name}")
    typer.echo(f"Markets: {len(market_ids)} tracked")
    for mid in market_ids:
        typer.echo(f"  {mid[:40]}...")
    typer.echo(f"Capital: ${config.initial_capital}")
    typer.echo(f"Order book refresh: {config.order_book_refresh_seconds}s")
    if max_ticks is not None:
        typer.echo(f"Max ticks: {max_ticks}")
    typer.echo("")

    feed = MarketFeed()
    try:
        async with PolymarketClient() as client:
            engine = PaperTradingEngine(client, pm_strategy, config, feed=feed)
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
