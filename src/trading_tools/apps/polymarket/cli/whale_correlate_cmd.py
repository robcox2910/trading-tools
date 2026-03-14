"""CLI command for correlating whale directional bets with spot price movement.

Fetch whale trades, compute per-market breakdowns, then correlate each
market's favoured side with actual BTC/ETH price direction from Binance
1-minute candles.
"""

import asyncio
import time
from typing import Annotated

import typer

from trading_tools.apps.polymarket.cli._helpers import require_whale_db_url
from trading_tools.apps.whale_monitor.analyser import MarketBreakdown, analyse_markets
from trading_tools.apps.whale_monitor.correlator import (
    correlate_markets,
    format_correlated_analysis,
)
from trading_tools.apps.whale_monitor.repository import WhaleRepository
from trading_tools.clients.binance.client import BinanceClient
from trading_tools.data.providers.binance import BinanceCandleProvider

_DEFAULT_DAYS = 1
_DEFAULT_MIN_TRADES = 10
_SECONDS_PER_DAY = 86400


def whale_correlate(
    address: Annotated[str, typer.Option(help="Whale proxy wallet address to analyse")],
    days: Annotated[int, typer.Option(help="Number of days to analyse")] = _DEFAULT_DAYS,
    min_trades: Annotated[
        int, typer.Option(help="Minimum trades per market to include")
    ] = _DEFAULT_MIN_TRADES,
    db_url: Annotated[str, typer.Option(help="SQLAlchemy async DB URL")] = "",
) -> None:
    """Correlate whale directional bets with actual spot price movement.

    Query trades for the given address, compute per-market breakdowns,
    then fetch Binance candles to determine whether the whale's favoured
    side matched the actual price direction during each market's window.
    """
    resolved_db_url = db_url or require_whale_db_url()

    async def _correlate() -> None:
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
        if markets.empty:
            typer.echo("No markets found matching the criteria.")
            return

        market_list = [
            MarketBreakdown(
                condition_id=str(row.condition_id),
                title=str(row.title),
                slug=str(row.slug),
                up_volume=float(row.up_volume),  # type: ignore[arg-type]
                down_volume=float(row.down_volume),  # type: ignore[arg-type]
                up_size=float(row.up_size),  # type: ignore[arg-type]
                down_size=float(row.down_size),  # type: ignore[arg-type]
                trade_count=int(row.trade_count),  # type: ignore[arg-type]
                bias_ratio=float(row.bias_ratio),  # type: ignore[arg-type]
                favoured_side=str(row.favoured_side),
                first_trade_ts=int(row.first_trade_ts),  # type: ignore[arg-type]
                last_trade_ts=int(row.last_trade_ts),  # type: ignore[arg-type]
            )
            for row in markets.itertuples(index=False)
        ]

        async with BinanceClient() as client:
            provider = BinanceCandleProvider(client)
            correlated = await correlate_markets(market_list, provider)

        typer.echo(format_correlated_analysis(correlated))

    asyncio.run(_correlate())
