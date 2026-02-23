"""CLI subpackage for the Polymarket prediction market app.

Create the Typer application and register all command modules.
"""

import typer

from trading_tools.apps.polymarket.cli.backtest_snipe_cmd import backtest_snipe
from trading_tools.apps.polymarket.cli.book_cmd import book
from trading_tools.apps.polymarket.cli.bot_cmd import bot
from trading_tools.apps.polymarket.cli.bot_live_cmd import bot_live
from trading_tools.apps.polymarket.cli.markets_cmd import markets
from trading_tools.apps.polymarket.cli.odds_cmd import odds
from trading_tools.apps.polymarket.cli.trade_cmd import balance, cancel, orders, trade

app = typer.Typer(help="Polymarket prediction market tools")

app.command()(markets)
app.command()(odds)
app.command()(book)
app.command()(bot)
app.command(name="bot-live")(bot_live)
app.command(name="backtest-snipe")(backtest_snipe)
app.command()(trade)
app.command()(balance)
app.command()(orders)
app.command()(cancel)

__all__ = ["app"]
