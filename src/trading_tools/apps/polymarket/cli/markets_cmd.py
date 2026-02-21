"""CLI command for searching Polymarket prediction markets.

List BTC prediction markets with current YES/NO prices, volume,
and end date in a tabular format.
"""

import asyncio
from typing import Annotated

import typer

from trading_tools.clients.polymarket.client import PolymarketClient

_DEFAULT_KEYWORD = "Bitcoin"
_DEFAULT_LIMIT = 20
_MAX_QUESTION_LEN = 58


def markets(
    keyword: Annotated[
        str, typer.Option(help="Search keyword for market questions")
    ] = _DEFAULT_KEYWORD,
    limit: Annotated[int, typer.Option(help="Maximum number of results")] = _DEFAULT_LIMIT,
) -> None:
    """Search for prediction markets matching a keyword."""
    asyncio.run(_markets(keyword=keyword, limit=limit))


async def _markets(*, keyword: str, limit: int) -> None:
    """Fetch and display matching prediction markets.

    Args:
        keyword: Search term to filter market questions.
        limit: Maximum number of results to display.

    """
    async with PolymarketClient() as client:
        results = await client.search_markets(keyword, limit=limit)

    if not results:
        typer.echo(f"No markets found for '{keyword}'")
        return

    typer.echo(f"\n{'Question':<60} {'YES':>6} {'NO':>6} {'Volume':>12} {'End Date':>12}")
    typer.echo("-" * 100)

    for market in results:
        yes_price = ""
        no_price = ""
        for token in market.tokens:
            if token.outcome.lower() == "yes":
                yes_price = f"{token.price:.2f}"
            elif token.outcome.lower() == "no":
                no_price = f"{token.price:.2f}"
        question = (
            market.question[:_MAX_QUESTION_LEN]
            if len(market.question) > _MAX_QUESTION_LEN
            else market.question
        )
        end_date = market.end_date[:10] if market.end_date else "N/A"
        typer.echo(
            f"{question:<60} {yes_price:>6} {no_price:>6} {market.volume:>12.0f} {end_date:>12}"
        )
