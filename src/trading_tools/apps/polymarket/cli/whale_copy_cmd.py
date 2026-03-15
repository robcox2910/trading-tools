"""CLI command for real-time whale copy-trading on Polymarket.

Run a polling service that monitors a whale's trades via the Polymarket
Data API directly, detects directional bias signals on BTC/ETH markets,
and copies them using temporal spread arbitrage. Defaults to paper mode;
pass ``--confirm-live`` for real orders.
"""

import asyncio
import time
from decimal import Decimal
from typing import Annotated

import typer

from trading_tools.apps.polymarket.cli._helpers import (
    build_authenticated_client,
    configure_logging,
)
from trading_tools.apps.whale_copy_trader.config import WhaleCopyConfig
from trading_tools.apps.whale_copy_trader.copy_trader import WhaleCopyTrader

_DEFAULT_POLL_INTERVAL = 5
_DEFAULT_LOOKBACK = 900
_DEFAULT_MIN_BIAS = "1.3"
_DEFAULT_MIN_TRADES = 2
_DEFAULT_MIN_TIME_TO_START = 0
_DEFAULT_CAPITAL = "100"
_DEFAULT_MAX_POSITION_PCT = "0.10"
_DEFAULT_MAX_WINDOW = 0
_DEFAULT_MAX_SPREAD_COST = "0.95"
_DEFAULT_MAX_ENTRY_PRICE = "0.65"
_LIVE_WARNING_DELAY = 2


def whale_copy(
    address: Annotated[str, typer.Option(help="Whale proxy wallet address to copy")],
    poll_interval: Annotated[
        int, typer.Option(help="Seconds between API polls (lower = faster)")
    ] = _DEFAULT_POLL_INTERVAL,
    lookback: Annotated[
        int, typer.Option(help="Rolling window in seconds for trade accumulation")
    ] = _DEFAULT_LOOKBACK,
    min_bias: Annotated[
        str, typer.Option(help="Minimum bias ratio to trigger a copy signal")
    ] = _DEFAULT_MIN_BIAS,
    min_trades: Annotated[
        int, typer.Option(help="Minimum trades per market to trigger a signal")
    ] = _DEFAULT_MIN_TRADES,
    min_time_to_start: Annotated[
        int, typer.Option(help="Min seconds before window opens to act on signal")
    ] = _DEFAULT_MIN_TIME_TO_START,
    capital: Annotated[
        str, typer.Option(help="Starting capital in USDC (paper mode)")
    ] = _DEFAULT_CAPITAL,
    max_position_pct: Annotated[
        str, typer.Option(help="Max fraction of capital per trade (e.g. 0.10)")
    ] = _DEFAULT_MAX_POSITION_PCT,
    max_window: Annotated[
        int, typer.Option(help="Max market window in seconds (e.g. 300 for 5-min only, 0=all)")
    ] = _DEFAULT_MAX_WINDOW,
    max_spread_cost: Annotated[
        str, typer.Option(help="Max combined cost of both legs to trigger hedge (e.g. 0.95)")
    ] = _DEFAULT_MAX_SPREAD_COST,
    max_entry_price: Annotated[
        str, typer.Option(help="Max price for directional entry (skip if above, e.g. 0.65)")
    ] = _DEFAULT_MAX_ENTRY_PRICE,
    confirm_live: Annotated[  # noqa: FBT002
        bool, typer.Option("--confirm-live", help="Enable LIVE trading with real orders")
    ] = False,
    verbose: Annotated[  # noqa: FBT002
        bool, typer.Option("--verbose", "-v", help="Enable DEBUG logging")
    ] = False,
) -> None:
    """Copy a whale's directional bets on BTC/ETH markets in real-time.

    Poll the Polymarket Data API directly for the whale's trades, detect
    directional bias signals, and copy them using temporal spread
    arbitrage. Paper mode by default; use ``--confirm-live`` for real
    Polymarket orders.
    """
    configure_logging(verbose=verbose)

    config = WhaleCopyConfig(
        whale_address=address,
        poll_interval=poll_interval,
        lookback_seconds=lookback,
        min_bias=Decimal(min_bias),
        min_trades=min_trades,
        min_time_to_start=min_time_to_start,
        capital=Decimal(capital),
        max_position_pct=Decimal(max_position_pct),
        max_window_seconds=max_window,
        max_spread_cost=Decimal(max_spread_cost),
        max_entry_price=Decimal(max_entry_price),
    )

    if confirm_live:
        typer.echo("=" * 60)
        typer.echo("  WARNING: LIVE TRADING MODE")
        typer.echo("  Real orders will be placed on Polymarket.")
        typer.echo(f"  Capital: ${config.capital}  Max/trade: {config.max_position_pct:.0%}")
        typer.echo("=" * 60)
        time.sleep(_LIVE_WARNING_DELAY)

    async def _run() -> None:
        # Both paper and live modes need a client for CLOB price data
        client = build_authenticated_client()

        trader = WhaleCopyTrader(
            config=config,
            live=confirm_live,
            client=client,
        )

        try:
            await trader.run()
        finally:
            await client.close()

    asyncio.run(_run())
