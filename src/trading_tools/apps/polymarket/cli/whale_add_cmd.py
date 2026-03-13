"""CLI command for adding a whale address to the monitoring database.

Register a new proxy wallet address with a friendly label in the whale
monitor database so it will be included in future polling cycles.
"""

import asyncio
from typing import Annotated

import typer

from trading_tools.apps.whale_monitor.repository import WhaleRepository

_DEFAULT_DB_URL = "sqlite+aiosqlite:///whale_data.db"


def whale_add(
    address: Annotated[str, typer.Option(help="Whale proxy wallet address")],
    label: Annotated[str, typer.Option(help="Friendly name for the whale")] = "",
    db_url: Annotated[str, typer.Option(help="SQLAlchemy async DB URL")] = _DEFAULT_DB_URL,
) -> None:
    """Add a whale address to the monitoring database.

    Register a proxy wallet address with an optional label. If no label
    is provided, the first 10 characters of the address are used.
    """
    if not label:
        label = address[:10]

    async def _add() -> None:
        repo = WhaleRepository(db_url)
        await repo.init_db()
        whale = await repo.add_whale(address, label)
        typer.echo(f"Whale registered: {whale.address} ({whale.label})")
        await repo.close()

    asyncio.run(_add())
