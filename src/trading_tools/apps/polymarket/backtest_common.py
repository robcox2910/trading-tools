"""Shared helpers for backtesting prediction market strategies.

Provide reusable functions for feeding snapshots to strategies, resolving
positions at window boundaries, building result summaries, parsing dates,
and displaying output. Used by both the candle-based ``backtest-snipe``
and tick-based ``backtest-ticks`` commands.
"""

import logging
from datetime import UTC, datetime
from decimal import Decimal

import typer

from trading_tools.apps.polymarket_bot.kelly import kelly_fraction
from trading_tools.apps.polymarket_bot.models import (
    MarketSnapshot,
    PaperTrade,
    PaperTradingResult,
)
from trading_tools.apps.polymarket_bot.portfolio import PaperPortfolio
from trading_tools.apps.polymarket_bot.strategies.late_snipe import PMLateSnipeStrategy
from trading_tools.core.models import ZERO, Side

logger = logging.getLogger(__name__)


def configure_verbose_logging() -> None:
    """Enable INFO-level logging for per-window backtest output."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")


def parse_date(value: str) -> int:
    """Parse a YYYY-MM-DD date string to a UTC epoch timestamp.

    Args:
        value: Date string in YYYY-MM-DD format.

    Returns:
        Unix epoch seconds at midnight UTC on the given date.

    Raises:
        typer.BadParameter: If the date cannot be parsed.

    """
    try:
        dt = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError as exc:
        msg = f"Invalid date format: {value!r} (expected YYYY-MM-DD)"
        raise typer.BadParameter(msg) from exc
    return int(dt.timestamp())


def feed_snapshot_to_strategy(
    snapshot: MarketSnapshot,
    strategy: PMLateSnipeStrategy,
    portfolio: PaperPortfolio,
    kelly_frac: Decimal,
    position_outcomes: dict[str, str],
) -> PaperTrade | None:
    """Feed a single snapshot to the strategy and open a position if signalled.

    Evaluate the snapshot against the strategy. If a signal is produced and no
    position is already open for this market, compute Kelly sizing and open
    a position in the portfolio.

    Args:
        snapshot: Market snapshot to evaluate.
        strategy: Late snipe strategy instance.
        portfolio: Paper portfolio for tracking positions.
        kelly_frac: Fractional Kelly multiplier for position sizing.
        position_outcomes: Mutable dict tracking condition_id → outcome
            (``"Yes"`` or ``"No"``). Updated when a position is opened.

    Returns:
        A ``PaperTrade`` if a position was opened, else ``None``.

    """
    signal = strategy.on_snapshot(snapshot, [])
    if signal is None:
        return None

    condition_id = snapshot.condition_id
    if condition_id in portfolio.positions:
        return None

    # Determine outcome and price
    if signal.side == Side.BUY:
        buy_price = snapshot.yes_price
        outcome = "Yes"
    elif signal.side == Side.SELL:
        buy_price = snapshot.no_price
        outcome = "No"
    else:
        return None

    # Kelly sizing
    estimated_prob = buy_price + signal.strength * (Decimal(1) - buy_price)
    estimated_prob = min(estimated_prob, Decimal("0.99"))
    fraction = kelly_fraction(
        estimated_prob,
        buy_price,
        fractional=kelly_frac,
    )
    if fraction <= ZERO:
        return None

    max_qty = portfolio.max_quantity_for(buy_price)
    quantity = max(Decimal(1), (max_qty * fraction).quantize(Decimal(1)))

    edge = estimated_prob - buy_price
    trade = portfolio.open_position(
        condition_id=condition_id,
        outcome=outcome,
        side=Side.BUY,
        price=buy_price,
        quantity=quantity,
        timestamp=snapshot.timestamp,
        reason=signal.reason,
        edge=edge,
    )
    if trade is not None:
        position_outcomes[condition_id] = outcome
        logger.info(
            "TRADE: %s %s qty=%s @ %.4f edge=%.4f",
            outcome,
            condition_id,
            quantity,
            buy_price,
            edge,
        )
    return trade


def resolve_positions(
    portfolio: PaperPortfolio,
    position_outcomes: dict[str, str],
    final_prices: dict[str, Decimal],
    resolve_ts: int,
) -> tuple[int, int]:
    """Close all open positions at resolution prices.

    Determine each position's outcome based on the final YES price:
    price > 0.5 means YES wins (resolve at 1.0), otherwise NO wins
    (resolve at 0.0).

    Args:
        portfolio: Paper portfolio with open positions.
        position_outcomes: Mapping from condition_id to outcome
            (``"Yes"`` or ``"No"``). Entries are consumed (popped).
        final_prices: Final YES price per condition_id for resolution.
        resolve_ts: Resolution timestamp in epoch seconds.

    Returns:
        Tuple of ``(wins, losses)`` counts.

    """
    wins = 0
    losses = 0

    for cid in list(portfolio.positions):
        final_yes = final_prices.get(cid, Decimal("0.5"))
        went_up = final_yes > Decimal("0.5")

        pos = portfolio.positions[cid]
        outcome = position_outcomes.pop(cid, "Yes")

        if outcome == "Yes":
            resolve_price = Decimal(1) if went_up else ZERO
        else:
            resolve_price = ZERO if went_up else Decimal(1)

        if resolve_price > pos.entry_price:
            wins += 1
        elif resolve_price < pos.entry_price:
            losses += 1

        trade = portfolio.close_position(cid, resolve_price, resolve_ts)
        if trade is not None:
            result_str = "WIN" if resolve_price > pos.entry_price else "LOSS"
            logger.info(
                "RESOLVE: %s %s @ %.2f (%s)",
                cid,
                outcome,
                resolve_price,
                result_str,
            )

    return wins, losses


def build_backtest_result(
    strategy_name: str,
    initial_capital: Decimal,
    portfolio: PaperPortfolio,
    snapshots_processed: int,
    windows_processed: int,
    wins: int,
    losses: int,
) -> PaperTradingResult:
    """Build the final backtest result with computed metrics.

    Assemble a ``PaperTradingResult`` from the portfolio state and
    win/loss counts accumulated during replay.

    Args:
        strategy_name: Name of the strategy that was run.
        initial_capital: Starting virtual capital.
        portfolio: Paper portfolio with all trades recorded.
        snapshots_processed: Total number of snapshots fed to the strategy.
        windows_processed: Number of market windows replayed.
        wins: Total winning position count.
        losses: Total losing position count.

    Returns:
        Summary of the backtest run with computed metrics.

    """
    trades = portfolio.trades
    final_capital = portfolio.total_equity
    total_trades = sum(1 for t in trades if t.side == Side.BUY)
    metrics: dict[str, Decimal] = {
        "windows_processed": Decimal(windows_processed),
        "total_trades": Decimal(total_trades),
        "wins": Decimal(wins),
        "losses": Decimal(losses),
    }
    if wins + losses > 0:
        metrics["win_rate"] = Decimal(wins) / Decimal(wins + losses)
    if total_trades > 0:
        total_return = final_capital - initial_capital
        metrics["total_return"] = total_return

    return PaperTradingResult(
        strategy_name=strategy_name,
        initial_capital=initial_capital,
        final_capital=final_capital,
        trades=tuple(trades),
        snapshots_processed=snapshots_processed,
        metrics=metrics,
    )


def display_result(result: PaperTradingResult) -> None:
    """Display backtest results to the terminal.

    Args:
        result: Completed backtest result.

    """
    typer.echo("\n--- Backtest Results ---")
    typer.echo(f"Strategy: {result.strategy_name}")
    typer.echo(f"Snapshots processed: {result.snapshots_processed}")
    typer.echo(f"Initial capital: ${result.initial_capital:.2f}")
    typer.echo(f"Final capital:   ${result.final_capital:.2f}")

    pnl = result.final_capital - result.initial_capital
    pnl_pct = pnl / result.initial_capital * Decimal(100) if result.initial_capital > ZERO else ZERO
    typer.echo(f"P&L: ${pnl:.2f} ({pnl_pct:.2f}%)")

    if result.metrics:
        typer.echo(
            f"\nWindows processed: {result.metrics.get('windows_processed', 0)}",
        )
        typer.echo(f"Total trades: {result.metrics.get('total_trades', 0)}")
        wins = result.metrics.get("wins", ZERO)
        losses = result.metrics.get("losses", ZERO)
        typer.echo(f"Wins: {wins}  Losses: {losses}")
        if "win_rate" in result.metrics:
            win_rate = result.metrics["win_rate"] * Decimal(100)
            typer.echo(f"Win rate: {win_rate:.1f}%")
