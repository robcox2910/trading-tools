"""CLI command for analysing a whale's trading strategy.

Run strategy analysis on stored whale trade data and display a formatted
report covering side bias, market types, outcome distribution, sizing
statistics, and hourly activity patterns.
"""

import asyncio
import os
import time
from typing import Annotated

import typer

from trading_tools.apps.whale_monitor.analyser import analyse_trades, format_analysis
from trading_tools.apps.whale_monitor.repository import WhaleRepository

_DEFAULT_DB_URL = os.environ.get("WHALE_DB_URL", "sqlite+aiosqlite:///whale_data.db")
_DEFAULT_DAYS = 7
_SECONDS_PER_DAY = 86400


def whale_analyse(
    address: Annotated[str, typer.Option(help="Whale proxy wallet address to analyse")],
    days: Annotated[int, typer.Option(help="Number of days to analyse")] = _DEFAULT_DAYS,
    db_url: Annotated[str, typer.Option(help="SQLAlchemy async DB URL")] = _DEFAULT_DB_URL,
) -> None:
    """Analyse a whale's trading strategy from stored data.

    Query trades for the given address within the specified time window
    and output a formatted analysis covering market types, side bias,
    outcome distribution, sizing, and timing patterns.
    """

    async def _analyse() -> None:
        repo = WhaleRepository(db_url)
        await repo.init_db()

        now = int(time.time())
        start_ts = now - (days * _SECONDS_PER_DAY)

        trades = await repo.get_trades(address, start_ts, now)
        await repo.close()

        if not trades:
            typer.echo(f"No trades found for {address} in the last {days} days.")
            return

        analysis = analyse_trades(address, trades)
        typer.echo(format_analysis(analysis))

    asyncio.run(_analyse())
