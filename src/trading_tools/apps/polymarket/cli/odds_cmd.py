"""CLI command for displaying prediction market odds.

Show detailed odds for a specific market: YES/NO prices, midpoint,
spread, and implied probabilities.
"""

import asyncio

import typer

from trading_tools.clients.polymarket.client import PolymarketClient
from trading_tools.clients.polymarket.exceptions import PolymarketAPIError

_PERCENTAGE_MULTIPLIER = 100


def odds(condition_id: str) -> None:
    """Display detailed odds for a prediction market.

    Args:
        condition_id: Unique identifier for the market condition.

    """
    asyncio.run(_odds(condition_id=condition_id))


async def _odds(*, condition_id: str) -> None:
    """Fetch and display detailed odds for a market.

    Args:
        condition_id: Unique identifier for the market condition.

    """
    try:
        async with PolymarketClient() as client:
            market = await client.get_market(condition_id)
    except PolymarketAPIError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"\n{market.question}")
    typer.echo(f"{'=' * len(market.question)}")
    typer.echo(f"Condition ID: {market.condition_id}")
    typer.echo(f"End Date:     {market.end_date}")
    typer.echo(f"Volume:       ${market.volume:,.0f}")
    typer.echo(f"Liquidity:    ${market.liquidity:,.0f}")
    typer.echo(f"Active:       {'Yes' if market.active else 'No'}")
    typer.echo("")

    typer.echo(f"{'Outcome':<10} {'Price':>8} {'Implied Prob':>14}")
    typer.echo("-" * 34)
    for token in market.tokens:
        prob = token.price * _PERCENTAGE_MULTIPLIER
        typer.echo(f"{token.outcome:<10} {token.price:>8.4f} {prob:>13.1f}%")
