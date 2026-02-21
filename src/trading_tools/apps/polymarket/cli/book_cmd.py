"""CLI command for displaying a Polymarket token order book.

Show top bids and asks with sizes, spread, and midpoint for a
given CLOB token.
"""

import asyncio
from typing import Annotated

import typer

from trading_tools.clients.polymarket.client import PolymarketClient
from trading_tools.clients.polymarket.exceptions import PolymarketAPIError

_DEFAULT_DEPTH = 10


def book(
    token_id: str,
    depth: Annotated[int, typer.Option(help="Number of price levels to display")] = _DEFAULT_DEPTH,
) -> None:
    """Display the order book for a token.

    Args:
        token_id: CLOB token identifier.
        depth: Number of price levels to show on each side.

    """
    asyncio.run(_book(token_id=token_id, depth=depth))


async def _book(*, token_id: str, depth: int) -> None:
    """Fetch and display the order book for a token.

    Args:
        token_id: CLOB token identifier.
        depth: Number of price levels to show on each side.

    """
    try:
        async with PolymarketClient() as client:
            order_book = await client.get_order_book(token_id)
    except PolymarketAPIError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"\nOrder Book: {order_book.token_id}")
    typer.echo(f"Spread: {order_book.spread:.4f}  |  Midpoint: {order_book.midpoint:.4f}")
    typer.echo("")

    typer.echo(f"{'BIDS':<30} {'ASKS':>30}")
    typer.echo(f"{'Price':>8} {'Size':>10}{'':>12}{'Price':>8} {'Size':>10}")
    typer.echo("-" * 60)

    bids = order_book.bids[:depth]
    asks = order_book.asks[:depth]
    max_rows = max(len(bids), len(asks))

    for i in range(max_rows):
        bid_str = f"{bids[i].price:>8.4f} {bids[i].size:>10.2f}" if i < len(bids) else " " * 19
        ask_str = f"{asks[i].price:>8.4f} {asks[i].size:>10.2f}" if i < len(asks) else ""
        typer.echo(f"{bid_str}{'':>12}{ask_str}")
