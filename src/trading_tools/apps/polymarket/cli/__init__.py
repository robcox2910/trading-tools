"""CLI subpackage for the Polymarket prediction market app.

Create the Typer application and register all command modules.
"""

import typer

from trading_tools.apps.polymarket.cli.backtest_snipe_cmd import backtest_snipe
from trading_tools.apps.polymarket.cli.backtest_ticks_cmd import backtest_ticks
from trading_tools.apps.polymarket.cli.book_cmd import book
from trading_tools.apps.polymarket.cli.bot_cmd import bot
from trading_tools.apps.polymarket.cli.bot_live_cmd import bot_live
from trading_tools.apps.polymarket.cli.directional_backtest_cmd import directional_backtest
from trading_tools.apps.polymarket.cli.directional_cmd import directional
from trading_tools.apps.polymarket.cli.directional_grid_cmd import directional_grid
from trading_tools.apps.polymarket.cli.grid_backtest_cmd import grid_backtest
from trading_tools.apps.polymarket.cli.markets_cmd import markets
from trading_tools.apps.polymarket.cli.odds_cmd import odds
from trading_tools.apps.polymarket.cli.spread_backtest_cmd import backtest_spread
from trading_tools.apps.polymarket.cli.spread_capture_cmd import spread_capture
from trading_tools.apps.polymarket.cli.spread_grid_cmd import grid_spread
from trading_tools.apps.polymarket.cli.tick_collector_cmd import tick_collect
from trading_tools.apps.polymarket.cli.trade_cmd import balance, cancel, orders, redeem, trade
from trading_tools.apps.polymarket.cli.whale_add_cmd import whale_add
from trading_tools.apps.polymarket.cli.whale_analyse_cmd import whale_analyse
from trading_tools.apps.polymarket.cli.whale_copy_cmd import whale_copy
from trading_tools.apps.polymarket.cli.whale_correlate_cmd import whale_correlate
from trading_tools.apps.polymarket.cli.whale_markets_cmd import whale_markets
from trading_tools.apps.polymarket.cli.whale_monitor_cmd import whale_monitor

app = typer.Typer(help="Polymarket prediction market tools")

app.command()(markets)
app.command()(odds)
app.command()(book)
app.command()(bot)
app.command(name="bot-live")(bot_live)
app.command(name="backtest-snipe")(backtest_snipe)
app.command(name="backtest-ticks")(backtest_ticks)
app.command(name="grid-backtest")(grid_backtest)
app.command(name="tick-collect")(tick_collect)
app.command()(trade)
app.command()(balance)
app.command()(orders)
app.command()(cancel)
app.command()(redeem)
app.command(name="whale-monitor")(whale_monitor)
app.command(name="whale-add")(whale_add)
app.command(name="whale-analyse")(whale_analyse)
app.command(name="whale-markets")(whale_markets)
app.command(name="spread-capture")(spread_capture)
app.command(name="whale-correlate")(whale_correlate)
app.command(name="backtest-spread")(backtest_spread)
app.command(name="grid-spread")(grid_spread)
app.command(name="directional-backtest")(directional_backtest)
app.command(name="directional")(directional)
app.command(name="whale-copy")(whale_copy)
app.command(name="directional-grid")(directional_grid)

__all__ = ["app"]
