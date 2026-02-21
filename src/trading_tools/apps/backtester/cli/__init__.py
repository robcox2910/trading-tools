"""CLI subpackage for the backtester.

Create the Typer application and register all four command modules.
Import order matters: the ``app`` must exist before commands reference it.
"""

import typer

from trading_tools.apps.backtester.cli.compare_cmd import compare
from trading_tools.apps.backtester.cli.monte_carlo_cmd import monte_carlo_cmd
from trading_tools.apps.backtester.cli.run_cmd import run
from trading_tools.apps.backtester.cli.walk_forward_cmd import walk_forward_cmd

app = typer.Typer(help="Run a backtest")

app.command()(run)
app.command()(compare)
app.command("monte-carlo")(monte_carlo_cmd)
app.command("walk-forward")(walk_forward_cmd)

__all__ = ["app"]
