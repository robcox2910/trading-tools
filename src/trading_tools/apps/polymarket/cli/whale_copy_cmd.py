"""CLI command for real-time whale copy-trading on Polymarket.

Run a polling service that monitors a whale's trades, detects directional
bias signals on BTC/ETH 5-minute markets, and copies them. Defaults to
paper mode (virtual P&L tracking); pass ``--confirm-live`` for real orders.
"""

import asyncio
import time
from decimal import Decimal
from typing import Annotated

import typer

from trading_tools.apps.polymarket.cli._helpers import (
    build_authenticated_client,
    configure_logging,
    require_whale_db_url,
)
from trading_tools.apps.whale_copy_trader.config import WhaleCopyConfig
from trading_tools.apps.whale_copy_trader.copy_trader import WhaleCopyTrader
from trading_tools.apps.whale_monitor.repository import WhaleRepository

_DEFAULT_POLL_INTERVAL = 5
_DEFAULT_LOOKBACK = 300
_DEFAULT_MIN_BIAS = "1.3"
_DEFAULT_MIN_TRADES = 2
_DEFAULT_MIN_TIME_TO_START = 60
_DEFAULT_CAPITAL = "100"
_DEFAULT_MAX_POSITION_PCT = "0.10"
_LIVE_WARNING_DELAY = 2


def whale_copy(
    address: Annotated[str, typer.Option(help="Whale proxy wallet address to copy")],
    poll_interval: Annotated[
        int, typer.Option(help="Seconds between DB polls (lower = faster)")
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
    confirm_live: Annotated[  # noqa: FBT002
        bool, typer.Option("--confirm-live", help="Enable LIVE trading with real orders")
    ] = False,
    db_url: Annotated[str, typer.Option(help="SQLAlchemy async DB URL")] = "",
    verbose: Annotated[  # noqa: FBT002
        bool, typer.Option("--verbose", "-v", help="Enable DEBUG logging")
    ] = False,
) -> None:
    """Copy a whale's directional bets on BTC/ETH 5-minute markets.

    Poll the whale_trades database for new trades, detect markets with
    strong directional bias, and copy them. Paper mode by default;
    use ``--confirm-live`` for real Polymarket orders.
    """
    resolved_db_url = db_url or require_whale_db_url()
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
    )

    if confirm_live:
        typer.echo("=" * 60)
        typer.echo("  WARNING: LIVE TRADING MODE")
        typer.echo("  Real orders will be placed on Polymarket.")
        typer.echo(f"  Capital: ${config.capital}  Max/trade: {config.max_position_pct:.0%}")
        typer.echo("=" * 60)
        time.sleep(_LIVE_WARNING_DELAY)

    async def _run() -> None:
        repo = WhaleRepository(resolved_db_url)
        await repo.init_db()

        client = None
        if confirm_live:
            client = build_authenticated_client()

        trader = WhaleCopyTrader(
            config=config,
            repo=repo,
            live=confirm_live,
            client=client,
        )

        try:
            await trader.run()
        finally:
            if client is not None:
                await client.close()
            await repo.close()

    asyncio.run(_run())
