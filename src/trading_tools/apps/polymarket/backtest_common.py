"""Shared helpers for backtesting prediction market strategies.

Provide reusable functions for feeding snapshots to strategies, resolving
positions at window boundaries, building result summaries, parsing dates,
displaying output, loading ticks from the database, checking order book
liquidity, and computing order book slippage. Used by ``backtest-snipe``,
``backtest-ticks``, and ``grid-backtest`` commands.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import typer

from trading_tools.apps.polymarket_bot.kelly import kelly_fraction
from trading_tools.apps.polymarket_bot.models import (
    MarketSnapshot,
    PaperTrade,
    PaperTradingResult,
)
from trading_tools.apps.tick_collector.repository import TickRepository
from trading_tools.core.models import ZERO, Side

if TYPE_CHECKING:
    from trading_tools.apps.polymarket_bot.portfolio import PaperPortfolio
    from trading_tools.apps.polymarket_bot.strategies.late_snipe import PMLateSnipeStrategy
    from trading_tools.apps.tick_collector.models import OrderBookSnapshot, Tick
    from trading_tools.clients.polymarket.models import OrderBook

logger = logging.getLogger(__name__)

_MS_PER_SECOND = 1000
_ONE = Decimal(1)


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


def _collect_fillable_levels(
    order_book: OrderBook,
    side: Side,
    price: Decimal,
) -> list[tuple[Decimal, Decimal]]:
    """Collect order book levels eligible to fill a trade.

    For BUY YES: return ask levels where ask price <= price, sorted cheapest
    first. For BUY NO (``Side.SELL``): return bid levels where bid price >=
    (1 - price), with prices converted to the NO complement, sorted cheapest
    first.

    Args:
        order_book: Order book snapshot for the YES token.
        side: Trade side — ``Side.BUY`` for YES, ``Side.SELL`` for NO.
        price: Maximum acceptable execution price.

    Returns:
        List of ``(fill_price, size)`` tuples in price-priority order
        (cheapest first).

    """
    if side == Side.BUY:
        levels = [(level.price, level.size) for level in order_book.asks if level.price <= price]
        levels.sort(key=lambda lv: lv[0])
    else:
        complement = _ONE - price
        levels = [
            (_ONE - level.price, level.size)
            for level in order_book.bids
            if level.price >= complement
        ]
        levels.sort(key=lambda lv: lv[0])
    return levels


def check_order_book_liquidity(
    order_book: OrderBook,
    side: Side,
    price: Decimal,
    quantity: Decimal,
) -> bool:
    """Check whether the order book can absorb a trade at the given price.

    For a BUY YES order, sum ask sizes where ask price <= price.
    For a BUY NO order (``Side.SELL``), use YES bids as proxy — sum bid
    sizes where bid price >= (1 - price).

    Args:
        order_book: Order book snapshot for the YES token.
        side: Trade side — ``Side.BUY`` for YES, ``Side.SELL`` for NO.
        price: Execution price of the trade.
        quantity: Number of tokens to trade.

    Returns:
        ``True`` if the available liquidity is sufficient, ``False`` otherwise.

    """
    if not order_book.asks and not order_book.bids:
        return False

    levels = _collect_fillable_levels(order_book, side, price)
    available = sum((size for _, size in levels), start=ZERO)
    return available >= quantity


def compute_order_book_slippage(
    order_book: OrderBook,
    side: Side,
    price: Decimal,
    quantity: Decimal,
) -> Decimal | None:
    """Compute the VWAP fill price by walking the order book.

    Walk eligible levels in price-priority order (cheapest asks first for
    BUY YES, highest bids converted to NO complement for BUY NO). Consume
    size from each level until the full quantity is filled and compute the
    volume-weighted average price.

    Args:
        order_book: Order book snapshot for the YES token.
        side: Trade side — ``Side.BUY`` for YES, ``Side.SELL`` for NO.
        price: Maximum acceptable execution price (snapshot mid-price).
        quantity: Number of tokens to fill.

    Returns:
        The VWAP fill price, or ``None`` if the book cannot fill the full
        quantity.

    """
    levels = _collect_fillable_levels(order_book, side, price)
    if not levels:
        return None

    filled = ZERO
    cost = ZERO
    for level_price, level_size in levels:
        take = min(level_size, quantity - filled)
        cost += level_price * take
        filled += take
        if filled >= quantity:
            break

    if filled < quantity:
        return None

    return cost / quantity


def feed_snapshot_to_strategy(
    snapshot: MarketSnapshot,
    strategy: PMLateSnipeStrategy,
    portfolio: PaperPortfolio,
    kelly_frac: Decimal,
    position_outcomes: dict[str, str],
    *,
    check_liquidity: bool = False,
    max_slippage: Decimal | None = None,
) -> PaperTrade | None:
    """Feed a single snapshot to the strategy and open a position if signalled.

    Evaluate the snapshot against the strategy. If a signal is produced and no
    position is already open for this market, compute Kelly sizing and open
    a position in the portfolio.

    When ``check_liquidity`` is ``True``, verify that the snapshot's order book
    has sufficient depth to absorb the computed quantity before opening the
    position. Skip the trade if liquidity is insufficient.

    When ``max_slippage`` is set and the order book is non-empty, compute a
    VWAP fill price by walking the book. Skip the trade if the book cannot
    fill the quantity or if slippage exceeds the tolerance. Use the VWAP
    price for edge computation and position entry.

    Args:
        snapshot: Market snapshot to evaluate.
        strategy: Late snipe strategy instance.
        portfolio: Paper portfolio for tracking positions.
        kelly_frac: Fractional Kelly multiplier for position sizing.
        position_outcomes: Mutable dict tracking condition_id → outcome
            (``"Yes"`` or ``"No"``). Updated when a position is opened.
        check_liquidity: When ``True``, validate order book depth before
            opening the position. Default ``False`` for backward compatibility.
        max_slippage: Maximum allowable slippage from the snapshot price.
            When ``None``, slippage modelling is disabled and the snapshot
            price is used directly.

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
    estimated_prob = buy_price + signal.strength * (_ONE - buy_price)
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

    if check_liquidity and not check_order_book_liquidity(
        snapshot.order_book, signal.side, buy_price, quantity
    ):
        logger.info(
            "SKIP (liquidity): %s %s qty=%s @ %.4f",
            outcome,
            condition_id,
            quantity,
            buy_price,
        )
        return None

    # Slippage modelling
    fill_price = buy_price
    slippage = ZERO
    book_has_levels = bool(snapshot.order_book.asks or snapshot.order_book.bids)
    if max_slippage is not None and book_has_levels:
        vwap = compute_order_book_slippage(snapshot.order_book, signal.side, buy_price, quantity)
        if vwap is None:
            logger.info(
                "SKIP (unfillable): %s %s qty=%s @ %.4f",
                outcome,
                condition_id,
                quantity,
                buy_price,
            )
            return None

        slippage = vwap - buy_price
        if slippage > max_slippage:
            logger.info(
                "SKIP (slippage %.4f > %.4f): %s %s qty=%s @ %.4f",
                slippage,
                max_slippage,
                outcome,
                condition_id,
                quantity,
                buy_price,
            )
            return None

        fill_price = vwap
        logger.info(
            "SLIPPAGE: %s %s slippage=%.4f vwap=%.4f",
            outcome,
            condition_id,
            slippage,
            vwap,
        )

    edge = estimated_prob - fill_price
    trade = portfolio.open_position(
        condition_id=condition_id,
        outcome=outcome,
        side=Side.BUY,
        price=fill_price,
        quantity=quantity,
        timestamp=snapshot.timestamp,
        reason=signal.reason,
        edge=edge,
        slippage=slippage,
    )
    if trade is not None:
        position_outcomes[condition_id] = outcome
        logger.info(
            "TRADE: %s %s qty=%s @ %.4f edge=%.4f",
            outcome,
            condition_id,
            quantity,
            fill_price,
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


async def load_ticks(
    db_url: str,
    start_ms: int,
    end_ms: int,
) -> tuple[dict[str, list[Tick]], dict[str, list[OrderBookSnapshot]]]:
    """Load ticks and order book snapshots from the database.

    Query the tick repository for all conditions within the time range,
    then load any corresponding order book snapshots for enrichment.

    Args:
        db_url: SQLAlchemy async connection string for the tick database.
        start_ms: Inclusive lower bound (epoch milliseconds).
        end_ms: Inclusive upper bound (epoch milliseconds).

    Returns:
        Tuple of (ticks_by_condition_id, book_snapshots_by_token_id).
        Book snapshots may be empty if none were captured.

    """
    repo = TickRepository(db_url)
    try:
        condition_ids = await repo.get_distinct_condition_ids(start_ms, end_ms)
        tick_result: dict[str, list[Tick]] = {}
        all_asset_ids: set[str] = set()
        for cid in condition_ids:
            ticks = await repo.get_ticks_by_condition(cid, start_ms, end_ms)
            if ticks:
                tick_result[cid] = ticks
                all_asset_ids.update(t.asset_id for t in ticks)

        book_result: dict[str, list[OrderBookSnapshot]] = {}
        for asset_id in all_asset_ids:
            books = await repo.get_order_book_snapshots_in_range(asset_id, start_ms, end_ms)
            if books:
                book_result[asset_id] = books

        return tick_result, book_result
    finally:
        await repo.close()
