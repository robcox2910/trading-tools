"""CLI command for backtesting whale copy-trading using historical order book data.

Simulate copying a whale's trades at configurable time offsets after the whale
executes, using real captured order book snapshots to estimate fill price and
quantity.

Sizing model
------------
Copy size is capital-proportional: the whale's trade cost as a fraction of
their estimated wallet determines what fraction of our allocated capital to
deploy.  Example: whale spends $100 of $10 000 (1%); we have $500 allocated
→ we deploy $5.

Outputs a per-offset summary table showing budget utilisation, average
slippage, and worst-case slippage across all trades.
"""

from __future__ import annotations

import asyncio
import os
import time
from decimal import Decimal
from typing import TYPE_CHECKING, Annotated

import typer

from trading_tools.apps.polymarket.cli._helpers import require_whale_db_url
from trading_tools.apps.tick_collector.repository import TickRepository
from trading_tools.apps.whale_copy_trader.copy_backtest_engine import CopyBacktestEngine
from trading_tools.apps.whale_monitor.repository import WhaleRepository

if TYPE_CHECKING:
    from trading_tools.apps.whale_copy_trader.copy_backtest_models import (
        CopyBacktestResult,
        OffsetStats,
    )

_DEFAULT_DAYS = 7
_DEFAULT_WHALE_CAPITAL = "10000"
_DEFAULT_OUR_CAPITAL = "500"
_DEFAULT_OFFSETS = "500,1000,5000"
_DEFAULT_OB_TOLERANCE_MS = 2000
_SECONDS_PER_DAY = 86400
_DEFAULT_TICK_DB_URL = os.environ.get("TICK_DB_URL", "sqlite+aiosqlite:///tick_data.db")


def _parse_offsets(offsets_str: str) -> list[int]:
    """Parse a comma-separated string of integer millisecond offsets.

    Args:
        offsets_str: Comma-separated integers, e.g. ``"500,1000,5000"``.

    Returns:
        Sorted list of integer millisecond offsets.

    Raises:
        typer.BadParameter: If any token cannot be parsed as an integer.

    """
    try:
        return sorted(int(x.strip()) for x in offsets_str.split(",") if x.strip())
    except ValueError as exc:
        msg = f"Offsets must be comma-separated integers, got: {offsets_str!r}"
        raise typer.BadParameter(msg) from exc


def _format_result(result: CopyBacktestResult) -> str:
    """Format the backtest result as a human-readable terminal table.

    Args:
        result: Completed copy-trading backtest result.

    Returns:
        Multi-line string ready for ``typer.echo``.

    """
    lines: list[str] = []
    lines.append("")
    lines.append("=" * 76)
    lines.append("Copy-Trading Backtest Results")
    lines.append("=" * 76)
    lines.append(f"Whale address  : {result.whale_address}")
    lines.append(f"Whale capital  : ${float(result.whale_capital):,.2f} (estimated)")
    lines.append(f"Our capital    : ${float(result.our_capital):,.2f} (allocated)")
    lines.append(f"Total trades   : {result.total_trades}")

    if result.total_trades == 0:
        lines.append("")
        lines.append("No trades found for this address in the given period.")
        lines.append("=" * 76)
        return "\n".join(lines)

    # Show trade-level sizing context using first 3 trades as examples
    sample_trades = list(result.trades[:3])
    if sample_trades:
        lines.append("")
        lines.append("Example trade sizing:")
        lines.extend(
            f"  {t.title[:40]:<40}  "
            f"whale=${float(t.whale_trade_cost):.2f} "
            f"({float(t.whale_fraction) * 100:.2f}% of wallet) "
            f"-> our budget=${float(t.our_budget_usd):.4f}"
            for t in sample_trades
        )

    lines.append("")

    # Summary table
    col = 13
    header = (
        f"{'Offset (ms)':<{col}}"
        f"{'OB Found':<{col}}"
        f"{'Full Deploy':<{col}}"
        f"{'Partial':<{col}}"
        f"{'Avg Util%':<{col}}"
        f"{'Deployed $':<{col}}"
        f"{'Avg Slip':<{col}}"
        f"{'Slip bp':<{col}}"
        f"{'Max Slip':<{col}}"
    )
    lines.append(header)
    lines.append("-" * 76)

    for offset_ms in result.offsets_ms:
        stats: OffsetStats = result.stats_by_offset[offset_ms]
        ob_str = (
            f"{stats.trades_with_book}/{stats.trades_total}"
            f" ({100 * stats.trades_with_book // max(stats.trades_total, 1)}%)"
        )
        lines.append(
            f"{offset_ms:<{col}}"
            f"{ob_str:<{col}}"
            f"{stats.trades_fully_deployed:<{col}}"
            f"{stats.trades_partial:<{col}}"
            f"{float(stats.avg_budget_utilisation) * 100:>{col - 2}.1f}%  "
            f"${float(stats.total_deployed_usd):>{col - 3}.4f}  "
            f"{float(stats.avg_slippage):>{col - 2}.4f}  "
            f"{float(stats.avg_slippage_bps):>{col - 2}.1f}  "
            f"{float(stats.max_slippage):>{col - 2}.4f}  "
        )

    lines.append("")
    lines.append("Slippage = our VWAP fill price minus whale price (BUY).")
    lines.append("Positive = we paid more than the whale.")
    lines.append("=" * 76)
    return "\n".join(lines)


def copy_backtest(
    address: Annotated[str, typer.Option(help="Whale proxy wallet address to analyse")],
    days: Annotated[int, typer.Option(help="Number of days of history to load")] = _DEFAULT_DAYS,
    whale_capital: Annotated[
        str,
        typer.Option(
            help="Estimated total USD wallet size of the whale, e.g. 10000. "
            "Used to compute each trade as a fraction of their portfolio."
        ),
    ] = _DEFAULT_WHALE_CAPITAL,
    our_capital: Annotated[
        str,
        typer.Option(
            help="USD capital we have allocated to copy-trading this whale, e.g. 500. "
            "We deploy (whale_trade / whale_capital) * our_capital per trade."
        ),
    ] = _DEFAULT_OUR_CAPITAL,
    offsets: Annotated[
        str,
        typer.Option(
            help="Comma-separated millisecond offsets after whale trade to simulate, "
            "e.g. 500,1000,5000"
        ),
    ] = _DEFAULT_OFFSETS,
    ob_tolerance_ms: Annotated[
        int,
        typer.Option(
            help="Max milliseconds from target timestamp to accept an order book snapshot"
        ),
    ] = _DEFAULT_OB_TOLERANCE_MS,
    whale_db_url: Annotated[str, typer.Option(help="SQLAlchemy async URL for whale database")] = "",
    tick_db_url: Annotated[
        str, typer.Option(help="SQLAlchemy async URL for tick/order-book database")
    ] = _DEFAULT_TICK_DB_URL,
) -> None:
    r"""Backtest copy-trading a whale using historical order book snapshots.

    For each trade placed by ``address``, compute a capital-proportional copy
    size (based on the whale's trade as a fraction of their estimated wallet),
    then simulate filling that budget at each configured millisecond offset
    using real captured order book snapshots.

    Requires both a whale database (``WHALE_DB_URL``) and a tick/order-book
    database (``TICK_DB_URL``) to be accessible.

    Example::

        trading-tools-polymarket copy-backtest \
            --address 0xabc123... \
            --days 7 \
            --whale-capital 10000 \
            --our-capital 500 \
            --offsets 500,1000,5000
    """
    resolved_whale_db = whale_db_url or require_whale_db_url()
    whale_cap = Decimal(whale_capital)
    our_cap = Decimal(our_capital)
    offset_list = _parse_offsets(offsets)

    now = int(time.time())
    start_ts = now - days * _SECONDS_PER_DAY

    typer.echo(f"Loading trades for {address} over last {days} day(s)...")
    typer.echo(f"Sizing: whale wallet=${float(whale_cap):,.0f}  our capital=${float(our_cap):,.0f}")
    typer.echo(f"Simulating at offsets: {offset_list} ms")

    async def _run() -> CopyBacktestResult:
        whale_repo = WhaleRepository(resolved_whale_db)
        tick_repo = TickRepository(tick_db_url)
        try:
            await whale_repo.init_db()
            engine = CopyBacktestEngine(
                whale_repo=whale_repo,
                tick_repo=tick_repo,
                ob_tolerance_ms=ob_tolerance_ms,
            )
            return await engine.run(
                whale_address=address,
                start_ts=start_ts,
                end_ts=now,
                whale_capital=whale_cap,
                our_capital=our_cap,
                offsets_ms=offset_list,
            )
        finally:
            await whale_repo.close()
            await tick_repo.close()

    result = asyncio.run(_run())
    typer.echo(_format_result(result))
