"""CLI subpackage for the Polymarket prediction market app.

Create the Typer application and register all command modules.
"""

import typer

from trading_tools.apps.polymarket.cli.book_cmd import book
from trading_tools.apps.polymarket.cli.bot_cmd import bot
from trading_tools.apps.polymarket.cli.markets_cmd import markets
from trading_tools.apps.polymarket.cli.odds_cmd import odds

app = typer.Typer(help="Polymarket prediction market tools")

app.command()(markets)
app.command()(odds)
app.command()(book)
app.command()(bot)

__all__ = ["app"]
