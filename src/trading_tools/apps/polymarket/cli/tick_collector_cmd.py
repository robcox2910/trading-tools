"""CLI command for running the Polymarket tick collector service.

Launch the async tick collector that streams trade events from the Polymarket
CLOB WebSocket and persists them to a database. Support both static market
IDs and auto-discovery via Gamma API series slugs.
"""

import asyncio
import logging
from typing import Annotated

import typer

from trading_tools.apps.polymarket.cli._helpers import parse_series_slugs
from trading_tools.apps.tick_collector.collector import TickCollector
from trading_tools.apps.tick_collector.config import CollectorConfig

_DEFAULT_DB_URL = "sqlite+aiosqlite:///tick_data.db"


def tick_collect(
    markets: Annotated[
        str, typer.Option(help="Comma-separated condition IDs to subscribe to")
    ] = "",
    series: Annotated[
        str,
        typer.Option(help="Comma-separated series slugs for auto-discovery (e.g. btc-updown-5m)"),
    ] = "",
    db_url: Annotated[str, typer.Option(help="SQLAlchemy async DB URL")] = _DEFAULT_DB_URL,
    flush_interval: Annotated[int, typer.Option(help="Max seconds between DB flushes")] = 10,
    flush_batch_size: Annotated[
        int, typer.Option(help="Max ticks buffered before forced flush")
    ] = 100,
    discovery_interval: Annotated[
        int, typer.Option(help="Seconds between market re-discovery")
    ] = 300,
    discovery_lead: Annotated[
        int,
        typer.Option(help="Seconds before next 5-min boundary to trigger discovery"),
    ] = 30,
    verbose: Annotated[  # noqa: FBT002
        bool, typer.Option("--verbose", "-v", help="Enable debug logging")
    ] = False,
) -> None:
    """Run the Polymarket tick collector service.

    Stream trade events from the Polymarket CLOB WebSocket and persist
    them to a database for backtesting. Use ``--series crypto-5m`` to
    auto-discover all four crypto 5-minute series, or ``--markets`` to
    specify condition IDs directly. Both can be combined.
    """
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    market_ids = tuple(m.strip() for m in markets.split(",") if m.strip())
    series_slugs = parse_series_slugs(series)

    if not market_ids and not series_slugs:
        typer.echo(
            "Error: specify --markets or --series (e.g. --series crypto-5m)",
            err=True,
        )
        raise typer.Exit(code=1)

    config = CollectorConfig(
        db_url=db_url,
        markets=market_ids,
        series_slugs=series_slugs,
        discovery_interval_seconds=discovery_interval,
        flush_interval_seconds=flush_interval,
        flush_batch_size=flush_batch_size,
        discovery_lead_seconds=discovery_lead,
    )

    typer.echo(f"Starting tick collector (db: {db_url})")
    if market_ids:
        typer.echo(f"Static markets: {len(market_ids)}")
    if series_slugs:
        typer.echo(f"Series slugs: {', '.join(series_slugs)}")

    collector = TickCollector(config)
    asyncio.run(collector.run())
