"""CLI command for backtesting the late snipe strategy on real tick data.

Read captured Polymarket trade ticks from the SQLite database, convert them
to ``MarketSnapshot`` sequences via ``SnapshotBuilder``, and replay through
``PMLateSnipeStrategy`` + ``PaperPortfolio``. Unlike ``backtest-snipe`` which
uses synthetic snapshots from Binance candles, this command uses actual
prediction market prices.
"""

import asyncio
import logging
from decimal import Decimal
from typing import Annotated

import typer

from trading_tools.apps.polymarket.backtest_common import (
    build_backtest_result,
    configure_verbose_logging,
    display_result,
    feed_snapshot_to_strategy,
    parse_date,
    resolve_positions,
)
from trading_tools.apps.polymarket_bot.models import (
    MarketSnapshot,
    PaperTradingResult,
)
from trading_tools.apps.polymarket_bot.portfolio import PaperPortfolio
from trading_tools.apps.polymarket_bot.strategies.late_snipe import PMLateSnipeStrategy
from trading_tools.apps.tick_collector.models import Tick
from trading_tools.apps.tick_collector.repository import TickRepository
from trading_tools.apps.tick_collector.snapshot_builder import (
    MarketWindow,
    SnapshotBuilder,
)

logger = logging.getLogger(__name__)

_MS_PER_SECOND = 1000
_DEFAULT_DB_URL = "sqlite+aiosqlite:///tick_data.db"


class TickBacktestRunner:
    """Replay real tick-derived snapshots through the late snipe strategy.

    Process pre-built ``(MarketWindow, [MarketSnapshot])`` pairs, feed each
    snapshot to the strategy, apply Kelly sizing, and resolve positions at
    each window boundary.

    Args:
        strategy: Late snipe strategy instance.
        portfolio: Paper portfolio for tracking positions.
        kelly_frac: Fractional Kelly multiplier for position sizing.
        initial_capital: Starting virtual capital for the result summary.

    """

    def __init__(
        self,
        strategy: PMLateSnipeStrategy,
        portfolio: PaperPortfolio,
        kelly_frac: Decimal,
        initial_capital: Decimal,
    ) -> None:
        """Initialize the tick backtest runner.

        Args:
            strategy: Configured late snipe strategy.
            portfolio: Paper portfolio with initial capital.
            kelly_frac: Fractional Kelly multiplier.
            initial_capital: Starting capital for result metadata.

        """
        self._strategy = strategy
        self._portfolio = portfolio
        self._kelly_frac = kelly_frac
        self._initial_capital = initial_capital
        self._snapshots_processed = 0
        self._windows_processed = 0
        self._wins = 0
        self._losses = 0
        self._position_outcomes: dict[str, str] = {}

    def replay(
        self,
        windows: list[tuple[MarketWindow, list[MarketSnapshot]]],
    ) -> PaperTradingResult:
        """Run the full backtest replay across all market windows.

        Args:
            windows: List of (window, snapshots) pairs to replay.

        Returns:
            Summary of the backtest run with trades and metrics.

        """
        for window, snapshots in windows:
            self._process_window(window, snapshots)

        return build_backtest_result(
            strategy_name=self._strategy.name,
            initial_capital=self._initial_capital,
            portfolio=self._portfolio,
            snapshots_processed=self._snapshots_processed,
            windows_processed=self._windows_processed,
            wins=self._wins,
            losses=self._losses,
        )

    def _process_window(
        self,
        window: MarketWindow,
        snapshots: list[MarketSnapshot],
    ) -> None:
        """Process a single market window.

        Feed each snapshot to the strategy, then resolve all positions
        at the window boundary.

        Args:
            window: Market window metadata.
            snapshots: Time-ordered snapshots for this window.

        """
        self._windows_processed += 1

        for snapshot in snapshots:
            self._snapshots_processed += 1
            feed_snapshot_to_strategy(
                snapshot=snapshot,
                strategy=self._strategy,
                portfolio=self._portfolio,
                kelly_frac=self._kelly_frac,
                position_outcomes=self._position_outcomes,
            )

        # Build final-price map from last snapshot
        final_prices: dict[str, Decimal] = {}
        if snapshots:
            final_prices[window.condition_id] = snapshots[-1].yes_price

        resolve_ts = window.end_ms // _MS_PER_SECOND
        wins, losses = resolve_positions(
            portfolio=self._portfolio,
            position_outcomes=self._position_outcomes,
            final_prices=final_prices,
            resolve_ts=resolve_ts,
        )
        self._wins += wins
        self._losses += losses


async def _load_ticks(
    db_url: str,
    start_ms: int,
    end_ms: int,
) -> dict[str, list[Tick]]:
    """Load ticks from the database grouped by condition_id.

    Args:
        db_url: SQLAlchemy async connection string for the tick database.
        start_ms: Inclusive lower bound (epoch milliseconds).
        end_ms: Inclusive upper bound (epoch milliseconds).

    Returns:
        Mapping from condition_id to sorted list of ticks.

    """
    repo = TickRepository(db_url)
    try:
        condition_ids = await repo.get_distinct_condition_ids(start_ms, end_ms)
        result: dict[str, list[Tick]] = {}
        for cid in condition_ids:
            ticks = await repo.get_ticks_by_condition(cid, start_ms, end_ms)
            if ticks:
                result[cid] = ticks
        return result
    finally:
        await repo.close()


def _run_tick_backtest(
    all_ticks: dict[str, list[Tick]],
    *,
    capital: Decimal,
    snipe_threshold: Decimal,
    snipe_window: int,
    kelly_frac: Decimal,
    max_position_pct: Decimal,
    bucket_seconds: int,
) -> PaperTradingResult:
    """Run the synchronous tick-based backtest replay.

    Args:
        all_ticks: Mapping from condition_id to sorted ticks.
        capital: Initial virtual capital.
        snipe_threshold: Price threshold for the late snipe strategy.
        snipe_window: Seconds before market end to start sniping.
        kelly_frac: Fractional Kelly multiplier.
        max_position_pct: Maximum fraction of capital per market.
        bucket_seconds: Seconds per snapshot bucket.

    Returns:
        Summary of the backtest run.

    """
    strategy = PMLateSnipeStrategy(
        threshold=snipe_threshold,
        window_seconds=snipe_window,
    )
    portfolio = PaperPortfolio(capital, max_position_pct)
    builder = SnapshotBuilder(bucket_seconds=bucket_seconds)

    # Build windows and snapshots for each condition
    window_data: list[tuple[MarketWindow, list[MarketSnapshot]]] = []
    for condition_id, ticks in sorted(all_ticks.items()):
        window = builder.detect_window(condition_id, ticks)
        snapshots = builder.build_snapshots(ticks, window)
        window_data.append((window, snapshots))
        logger.info(
            "Window: %s  ticks=%d  snapshots=%d",
            condition_id[:20],
            len(ticks),
            len(snapshots),
        )

    runner = TickBacktestRunner(
        strategy=strategy,
        portfolio=portfolio,
        kelly_frac=kelly_frac,
        initial_capital=capital,
    )
    return runner.replay(window_data)


def backtest_ticks(
    start: Annotated[str, typer.Option(help="Start date YYYY-MM-DD")] = "",
    end: Annotated[str, typer.Option(help="End date YYYY-MM-DD")] = "",
    db_url: Annotated[
        str, typer.Option(help="SQLAlchemy async DB URL for tick data")
    ] = _DEFAULT_DB_URL,
    capital: Annotated[float, typer.Option(help="Initial virtual capital in USD")] = 1000.0,
    snipe_threshold: Annotated[
        float, typer.Option(help="Price threshold for late snipe (0.5-1.0)")
    ] = 0.8,
    snipe_window: Annotated[
        int, typer.Option(help="Seconds before market end to start sniping")
    ] = 90,
    bucket_seconds: Annotated[int, typer.Option(help="Seconds per snapshot bucket")] = 1,
    kelly_frac: Annotated[float, typer.Option(help="Fractional Kelly multiplier")] = 0.25,
    max_position_pct: Annotated[
        float, typer.Option(help="Max fraction of capital per market")
    ] = 0.1,
    verbose: Annotated[  # noqa: FBT002
        bool, typer.Option("--verbose", "-v", help="Enable per-trade logging")
    ] = False,
) -> None:
    """Backtest the late snipe strategy on real tick data.

    Read captured Polymarket trade ticks from the SQLite database,
    convert to market snapshots, and replay through the late snipe
    strategy with Kelly sizing.
    """
    if not start or not end:
        typer.echo("Error: --start and --end dates are required", err=True)
        raise typer.Exit(code=1)

    if verbose:
        configure_verbose_logging()

    start_ts = parse_date(start)
    end_ts = parse_date(end)
    if start_ts >= end_ts:
        typer.echo("Error: --start must be before --end", err=True)
        raise typer.Exit(code=1)

    start_ms = start_ts * _MS_PER_SECOND
    end_ms = end_ts * _MS_PER_SECOND

    typer.echo("Backtesting late snipe strategy on tick data")
    typer.echo(f"Period: {start} to {end}")
    typer.echo(f"DB: {db_url}")
    typer.echo(f"Threshold: {snipe_threshold}, Window: {snipe_window}s")
    typer.echo(f"Capital: ${capital}, Bucket: {bucket_seconds}s")
    typer.echo("")

    typer.echo("Loading ticks from database...")
    all_ticks = asyncio.run(_load_ticks(db_url, start_ms, end_ms))

    if not all_ticks:
        typer.echo("No ticks found in the specified date range.")
        return

    total_ticks = sum(len(t) for t in all_ticks.values())
    typer.echo(f"Found {len(all_ticks)} conditions with {total_ticks} ticks")

    result = _run_tick_backtest(
        all_ticks,
        capital=Decimal(str(capital)),
        snipe_threshold=Decimal(str(snipe_threshold)),
        snipe_window=snipe_window,
        kelly_frac=Decimal(str(kelly_frac)),
        max_position_pct=Decimal(str(max_position_pct)),
        bucket_seconds=bucket_seconds,
    )

    display_result(result)
