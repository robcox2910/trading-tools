"""CLI command for per-market directional analysis of whale trades.

Analyse a whale's trading activity broken down by market, showing volume
on each side (Up/Down), bias ratios, and favoured direction. Surfaces the
sizing asymmetry that aggregate stats hide.
"""

import asyncio
import time
from typing import Annotated

import typer

from trading_tools.apps.polymarket.cli._helpers import require_whale_db_url
from trading_tools.apps.whale_monitor.analyser import analyse_markets, format_market_analysis
from trading_tools.apps.whale_monitor.repository import WhaleRepository

_DEFAULT_DAYS = 1
_DEFAULT_MIN_TRADES = 10
_SECONDS_PER_DAY = 86400


def whale_markets(
    address: Annotated[str, typer.Option(help="Whale proxy wallet address to analyse")],
    days: Annotated[int, typer.Option(help="Number of days to analyse")] = _DEFAULT_DAYS,
    min_trades: Annotated[
        int, typer.Option(help="Minimum trades per market to include")
    ] = _DEFAULT_MIN_TRADES,
    db_url: Annotated[str, typer.Option(help="SQLAlchemy async DB URL")] = "",
) -> None:
    """Analyse a whale's per-market directional bets.

    Query trades for the given address within the specified time window
    and output a per-market breakdown showing volume on each side,
    bias ratios, and the favoured direction for each market.
    """
    resolved_db_url = db_url or require_whale_db_url()

    async def _analyse() -> None:
        repo = WhaleRepository(resolved_db_url)
        await repo.init_db()

        now = int(time.time())
        start_ts = now - (days * _SECONDS_PER_DAY)

        trades = await repo.get_trades(address, start_ts, now)
        await repo.close()

        if not trades:
            typer.echo(f"No trades found for {address} in the last {days} day(s).")
            return

        markets = analyse_markets(trades, min_trades=min_trades)
        typer.echo(format_market_analysis(markets))

    asyncio.run(_analyse())
