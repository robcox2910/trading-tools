"""CLI command for running the Polymarket live trading bot.

Launch the WebSocket-driven live trading engine with a configurable strategy,
market selection, and safety guardrails. Require ``--confirm-live`` to prevent
accidental live trading. Display a warning banner and initial USDC balance
before starting.
"""

import asyncio
from decimal import Decimal
from typing import Annotated

import typer

from trading_tools.apps.polymarket.cli._helpers import (
    build_authenticated_client,
    configure_verbose_logging,
    discover_markets,
    parse_series_slugs,
)
from trading_tools.apps.polymarket_bot.live_engine import LiveTradingEngine
from trading_tools.apps.polymarket_bot.models import BotConfig, LiveTradingResult
from trading_tools.apps.polymarket_bot.strategy_factory import (
    PM_STRATEGY_NAMES,
    build_pm_strategy,
)
from trading_tools.apps.tick_collector.ws_client import MarketFeed
from trading_tools.clients.polymarket.exceptions import PolymarketAPIError


def bot_live(  # noqa: PLR0913
    strategy: Annotated[
        str, typer.Option(help=f"Strategy name: {', '.join(PM_STRATEGY_NAMES)}")
    ] = "pm_late_snipe",
    markets: Annotated[str, typer.Option(help="Comma-separated condition IDs to track")] = "",
    series: Annotated[
        str,
        typer.Option(help="Comma-separated series slugs for auto-discovery (e.g. btc-updown-5m)"),
    ] = "",
    ob_refresh: Annotated[int, typer.Option(help="Seconds between order book refreshes")] = 30,
    max_ticks: Annotated[
        int | None, typer.Option(help="Stop after N ticks (None = unlimited)")
    ] = None,
    max_position_pct: Annotated[
        float, typer.Option(help="Max fraction of balance per market")
    ] = 0.1,
    kelly_frac: Annotated[float, typer.Option(help="Fractional Kelly multiplier")] = 0.25,
    max_loss_pct: Annotated[
        float, typer.Option(help="Max drawdown fraction before auto-stop (0-1)")
    ] = 0.10,
    market_orders: Annotated[  # noqa: FBT002
        bool, typer.Option("--market-orders/--limit-orders", help="Use FOK market or GTC limit")
    ] = True,
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
    confirm_live: Annotated[  # noqa: FBT002
        bool, typer.Option("--confirm-live", help="Required flag to enable live trading")
    ] = False,
    auto_redeem: Annotated[  # noqa: FBT002
        bool,
        typer.Option(
            "--auto-redeem/--no-auto-redeem",
            help="Redeem winning tokens on-chain via CTF contract on rotation",
        ),
    ] = True,
    verbose: Annotated[  # noqa: FBT002
        bool, typer.Option("--verbose", "-v", help="Enable tick-by-tick logging")
    ] = False,
) -> None:
    """Run the Polymarket live trading bot with real money.

    Place real CLOB orders using an authenticated wallet. Require
    ``--confirm-live`` to prevent accidental execution. The engine
    automatically stops when the loss limit is reached and closes all
    positions on exit.
    """
    if not confirm_live:
        typer.echo("Error: --confirm-live is required for live trading.", err=True)
        typer.echo("This flag prevents accidental live trading with real money.", err=True)
        raise typer.Exit(code=1)

    if verbose:
        configure_verbose_logging()

    asyncio.run(
        _bot_live(
            strategy=strategy,
            markets=markets,
            series=series,
            ob_refresh=ob_refresh,
            max_ticks=max_ticks,
            max_position_pct=max_position_pct,
            kelly_frac=kelly_frac,
            max_loss_pct=max_loss_pct,
            market_orders=market_orders,
            period=period,
            z_threshold=z_threshold,
            spread_pct=spread_pct,
            imbalance_threshold=imbalance_threshold,
            min_edge=min_edge,
            snipe_threshold=snipe_threshold,
            snipe_window=snipe_window,
            auto_redeem=auto_redeem,
        )
    )


def _display_banner(
    strategy_name: str,
    market_ids: tuple[str, ...],
    *,
    market_orders: bool,
    max_loss_pct: float,
    ob_refresh: int,
    max_ticks: int | None,
) -> None:
    """Display the live trading warning banner and configuration.

    Args:
        strategy_name: Name of the strategy being used.
        market_ids: Condition IDs being tracked.
        market_orders: Whether FOK market orders are used.
        max_loss_pct: Maximum loss percentage.
        ob_refresh: Order book refresh interval in seconds.
        max_ticks: Maximum ticks or None.

    """
    typer.echo("")
    typer.echo("=" * 60)
    typer.echo("  LIVE TRADING MODE -- real money at risk")
    typer.echo("=" * 60)
    typer.echo("")
    typer.echo(f"Strategy: {strategy_name}")
    typer.echo(f"Markets: {len(market_ids)} tracked")
    for mid in market_ids:
        typer.echo(f"  {mid[:40]}...")
    typer.echo(f"Order type: {'market (FOK)' if market_orders else 'limit (GTC)'}")
    typer.echo(f"Max loss: {max_loss_pct:.0%}")
    typer.echo(f"Order book refresh: {ob_refresh}s")
    if max_ticks is not None:
        typer.echo(f"Max ticks: {max_ticks}")


def _display_results(result: LiveTradingResult) -> None:
    """Display the live trading results summary.

    Args:
        result: Completed live trading result.

    """
    typer.echo("\n--- Live Trading Results ---")
    typer.echo(f"Strategy: {result.strategy_name}")
    typer.echo(f"Snapshots processed: {result.snapshots_processed}")
    typer.echo(f"Initial balance: ${result.initial_balance:.2f}")
    typer.echo(f"Final balance:   ${result.final_balance:.2f}")
    typer.echo(f"Total trades: {len(result.trades)}")

    if result.metrics:
        typer.echo("\nMetrics:")
        for key, value in result.metrics.items():
            typer.echo(f"  {key}: {value:.4f}")


async def _bot_live(  # noqa: PLR0913
    *,
    strategy: str,
    markets: str,
    series: str,
    ob_refresh: int,
    max_ticks: int | None,
    max_position_pct: float,
    kelly_frac: float,
    max_loss_pct: float,
    market_orders: bool,
    period: int,
    z_threshold: float,
    spread_pct: float,
    imbalance_threshold: float,
    min_edge: float,
    snipe_threshold: float,
    snipe_window: int,
    auto_redeem: bool,
) -> None:
    """Run the live trading bot asynchronously.

    Args:
        strategy: Strategy name to use.
        markets: Comma-separated condition IDs.
        series: Comma-separated series slugs for auto-discovery.
        ob_refresh: Seconds between order book refreshes.
        max_ticks: Maximum number of ticks.
        max_position_pct: Maximum position size as fraction of balance.
        kelly_frac: Fractional Kelly multiplier.
        max_loss_pct: Maximum drawdown before auto-stop.
        market_orders: Use FOK market orders or GTC limit orders.
        period: Rolling window period for mean reversion.
        z_threshold: Z-score threshold for mean reversion.
        spread_pct: Half-spread for market making.
        imbalance_threshold: Threshold for liquidity imbalance.
        min_edge: Minimum edge for cross-market arb.
        snipe_threshold: Price threshold for late snipe strategy.
        snipe_window: Window in seconds before market end for sniping.
        auto_redeem: Auto-redeem resolved positions on rotation.

    """
    market_ids = tuple(m.strip() for m in markets.split(",") if m.strip())
    market_end_times: tuple[tuple[str, str], ...] = ()
    series_slugs = parse_series_slugs(series)

    # Discover markets from series slugs — create the client once and
    # keep it open for both discovery and subsequent trading.
    client = build_authenticated_client()
    if series_slugs:
        try:
            discovered = await discover_markets(client, series_slugs)
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
        order_book_refresh_seconds=ob_refresh,
        snipe_window_seconds=snipe_window,
        max_position_pct=Decimal(str(max_position_pct)),
        kelly_fraction=Decimal(str(kelly_frac)),
        markets=market_ids,
        market_end_times=market_end_times,
        series_slugs=series_slugs,
    )

    _display_banner(
        pm_strategy.name,
        market_ids,
        market_orders=market_orders,
        max_loss_pct=max_loss_pct,
        ob_refresh=ob_refresh,
        max_ticks=max_ticks,
    )

    feed = MarketFeed()
    try:
        async with client:
            bal = await client.get_balance("COLLATERAL")
            typer.echo(f"USDC Balance: {bal.balance}")
            typer.echo("")

            engine = LiveTradingEngine(
                client,
                pm_strategy,
                config,
                feed=feed,
                max_loss_pct=Decimal(str(max_loss_pct)),
                use_market_orders=market_orders,
                auto_redeem=auto_redeem,
            )
            result = await engine.run(max_ticks=max_ticks)
    except PolymarketAPIError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    _display_results(result)
