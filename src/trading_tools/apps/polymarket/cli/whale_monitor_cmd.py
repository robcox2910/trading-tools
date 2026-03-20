"""CLI command for running the whale trade monitor service.

Launch the async whale monitor that polls the Polymarket Data API for trades
from tracked whale addresses and persists them to a database.
"""

import asyncio
from typing import Annotated

import typer

from trading_tools.apps.polymarket.cli._helpers import configure_logging, require_whale_db_url
from trading_tools.apps.whale_monitor.collector import WhaleMonitor
from trading_tools.apps.whale_monitor.config import WhaleMonitorConfig

_DEFAULT_POLL_INTERVAL = 120


def whale_monitor(
    whales: Annotated[str, typer.Option(help="Comma-separated whale proxy wallet addresses")] = "",
    db_url: Annotated[str, typer.Option(help="SQLAlchemy async DB URL")] = "",
    poll_interval: Annotated[
        int, typer.Option(help="Seconds between polling cycles")
    ] = _DEFAULT_POLL_INTERVAL,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Enable debug logging")] = False,
) -> None:
    """Run the whale trade monitor service.

    Poll the Polymarket Data API for trades from tracked whale addresses
    and persist them to a database. Whale addresses can be supplied via
    ``--whales`` or pre-loaded in the database with ``whale-add``.
    """
    resolved_db_url = db_url or require_whale_db_url()
    configure_logging(verbose=verbose)

    whale_addrs = tuple(w.strip().lower() for w in whales.split(",") if w.strip())

    config = WhaleMonitorConfig(
        db_url=resolved_db_url,
        whales=whale_addrs,
        poll_interval_seconds=poll_interval,
    )

    typer.echo(f"Starting whale monitor (db: {resolved_db_url})")
    if whale_addrs:
        typer.echo(f"CLI whales: {len(whale_addrs)}")
    typer.echo(f"Poll interval: {poll_interval}s")

    monitor = WhaleMonitor(config)
    asyncio.run(monitor.run())
