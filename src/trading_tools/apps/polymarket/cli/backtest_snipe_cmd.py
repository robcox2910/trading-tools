"""CLI command for backtesting the late snipe strategy on historical data.

Fetch 1-minute Binance candles, simulate synthetic prediction market snapshots,
and replay them through ``PMLateSnipeStrategy`` + ``PaperPortfolio`` to measure
win rate and P&L over a date range. No live Polymarket connection is required.
"""

import asyncio
import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

import typer

from trading_tools.apps.polymarket_bot.kelly import kelly_fraction
from trading_tools.apps.polymarket_bot.models import (
    MarketSnapshot,
    PaperTradingResult,
)
from trading_tools.apps.polymarket_bot.portfolio import PaperPortfolio
from trading_tools.apps.polymarket_bot.snapshot_simulator import SnapshotSimulator
from trading_tools.apps.polymarket_bot.strategies.late_snipe import PMLateSnipeStrategy
from trading_tools.clients.binance.client import BinanceClient
from trading_tools.core.models import ZERO, Candle, Interval, Side
from trading_tools.data.providers.binance import BinanceCandleProvider

logger = logging.getLogger(__name__)

_FIVE_MINUTES = 300
_DEFAULT_SYMBOLS = "BTC-USD,ETH-USD,SOL-USD,XRP-USD"


def _configure_verbose_logging() -> None:
    """Enable INFO-level logging for per-window backtest output."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")


def _parse_date(value: str) -> int:
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


def _group_candles_into_windows(
    candles: list[Candle],
    start_ts: int,
    end_ts: int,
) -> dict[int, list[Candle]]:
    """Group 1-minute candles into 5-minute aligned windows.

    Args:
        candles: Sorted list of 1-minute candles.
        start_ts: Start epoch seconds (inclusive).
        end_ts: End epoch seconds (exclusive).

    Returns:
        Mapping from window-open timestamp to the candles in that window.

    """
    windows: dict[int, list[Candle]] = {}
    for candle in candles:
        if candle.timestamp < start_ts or candle.timestamp >= end_ts:
            continue
        window_open = (candle.timestamp // _FIVE_MINUTES) * _FIVE_MINUTES
        windows.setdefault(window_open, []).append(candle)
    return windows


class BacktestRunner:
    """Replay synthetic market snapshots through the late snipe strategy.

    Iterate pre-generated snapshots window-by-window, apply the strategy
    and Kelly sizing, and resolve positions at each window boundary. This
    is a synchronous replay — no async polling or mock clients needed.

    Args:
        strategy: Late snipe strategy instance.
        portfolio: Paper portfolio for tracking positions.
        simulator: Snapshot simulator for converting candles.
        kelly_frac: Fractional Kelly multiplier for position sizing.

    """

    def __init__(
        self,
        strategy: PMLateSnipeStrategy,
        portfolio: PaperPortfolio,
        simulator: SnapshotSimulator,
        kelly_frac: Decimal,
    ) -> None:
        """Initialize the backtest runner.

        Args:
            strategy: Configured late snipe strategy.
            portfolio: Paper portfolio with initial capital.
            simulator: Snapshot simulator for candle conversion.
            kelly_frac: Fractional Kelly multiplier.

        """
        self._strategy = strategy
        self._portfolio = portfolio
        self._simulator = simulator
        self._kelly_frac = kelly_frac
        self._snapshots_processed = 0
        self._windows_processed = 0
        self._wins = 0
        self._losses = 0

    def replay(
        self,
        symbols: list[str],
        all_candles: dict[str, list[Candle]],
        start_ts: int,
        end_ts: int,
    ) -> PaperTradingResult:
        """Run the full backtest replay across all symbols and windows.

        Args:
            symbols: List of trading pair symbols.
            all_candles: Mapping from symbol to sorted 1-minute candles.
            start_ts: Start epoch seconds.
            end_ts: End epoch seconds.

        Returns:
            Summary of the backtest run with trades and metrics.

        """
        # Group candles by symbol and window
        symbol_windows: dict[str, dict[int, list[Candle]]] = {}
        for symbol in symbols:
            candles = all_candles.get(symbol, [])
            symbol_windows[symbol] = _group_candles_into_windows(
                candles,
                start_ts,
                end_ts,
            )

        # Collect all unique window timestamps and sort them
        all_window_ts: set[int] = set()
        for windows in symbol_windows.values():
            all_window_ts.update(windows.keys())
        sorted_windows = sorted(all_window_ts)

        for window_ts in sorted_windows:
            self._process_window(symbols, symbol_windows, window_ts)

        return self._build_result()

    def _process_window(
        self,
        symbols: list[str],
        symbol_windows: dict[str, dict[int, list[Candle]]],
        window_ts: int,
    ) -> None:
        """Process a single 5-minute window for all symbols.

        Generate snapshots, feed to strategy, apply signals, then resolve.

        Args:
            symbols: List of symbol names.
            symbol_windows: Pre-grouped candle windows per symbol.
            window_ts: Window-open timestamp.

        """
        self._windows_processed += 1
        window_snapshots: dict[str, list[MarketSnapshot]] = {}

        for symbol in symbols:
            candles = symbol_windows.get(symbol, {}).get(window_ts)
            if not candles:
                continue
            snapshots = self._simulator.simulate_window(symbol, window_ts, candles)
            window_snapshots[symbol] = snapshots

        # Feed snapshots tick-by-tick (interleaved by minute index)
        max_ticks = max(
            (len(snaps) for snaps in window_snapshots.values()),
            default=0,
        )
        for tick_idx in range(max_ticks):
            for symbol in symbols:
                snaps = window_snapshots.get(symbol)
                if snaps is None or tick_idx >= len(snaps):
                    continue
                snapshot = snaps[tick_idx]
                self._snapshots_processed += 1
                self._feed_snapshot(snapshot)

        # Resolve all positions at window end
        self._resolve_window(window_snapshots, window_ts)

    def _feed_snapshot(self, snapshot: MarketSnapshot) -> None:
        """Feed a single snapshot to the strategy and apply any signal.

        Args:
            snapshot: Synthetic market snapshot.

        """
        signal = self._strategy.on_snapshot(snapshot, [])
        if signal is None:
            return

        condition_id = snapshot.condition_id
        if condition_id in self._portfolio.positions:
            return

        # Determine outcome and price
        if signal.side == Side.BUY:
            buy_price = snapshot.yes_price
            outcome = "Yes"
        elif signal.side == Side.SELL:
            buy_price = snapshot.no_price
            outcome = "No"
        else:
            return

        # Kelly sizing
        estimated_prob = buy_price + signal.strength * (Decimal(1) - buy_price)
        estimated_prob = min(estimated_prob, Decimal("0.99"))
        fraction = kelly_fraction(
            estimated_prob,
            buy_price,
            fractional=self._kelly_frac,
        )
        if fraction <= ZERO:
            return

        max_qty = self._portfolio.max_quantity_for(buy_price)
        quantity = max(Decimal(1), (max_qty * fraction).quantize(Decimal(1)))

        edge = estimated_prob - buy_price
        trade = self._portfolio.open_position(
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
            logger.info(
                "TRADE: %s %s qty=%s @ %.4f edge=%.4f",
                outcome,
                condition_id,
                quantity,
                buy_price,
                edge,
            )

    def _resolve_window(
        self,
        window_snapshots: dict[str, list[MarketSnapshot]],
        window_ts: int,
    ) -> None:
        """Close all open positions at resolution prices.

        At window end, determine if the underlying price went up or down.
        Resolve YES positions at 1.0 (win) or 0.0 (loss), and NO positions
        at 0.0 (win) or 1.0 (loss).

        Args:
            window_snapshots: Snapshots generated for this window.
            window_ts: Window-open timestamp.

        """
        resolve_ts = window_ts + _FIVE_MINUTES
        for cid in list(self._portfolio.positions):
            # Determine resolution from the final snapshot
            symbol = cid.rsplit("_", 1)[0]
            snaps = window_snapshots.get(symbol, [])
            if snaps:
                final_yes = snaps[-1].yes_price
                went_up = final_yes > Decimal("0.5")
            else:
                went_up = True  # default if no data

            # Get outcome of the position from portfolio trades
            pos = self._portfolio.positions[cid]
            # Find the opening trade to determine outcome
            outcome = self._get_position_outcome(cid)

            if outcome == "Yes":
                resolve_price = Decimal(1) if went_up else ZERO
            else:
                resolve_price = ZERO if went_up else Decimal(1)

            # Track wins/losses
            if resolve_price > pos.entry_price:
                self._wins += 1
            elif resolve_price < pos.entry_price:
                self._losses += 1

            trade = self._portfolio.close_position(cid, resolve_price, resolve_ts)
            if trade is not None:
                result_str = "WIN" if resolve_price > pos.entry_price else "LOSS"
                logger.info(
                    "RESOLVE: %s %s @ %.2f (%s)",
                    cid,
                    outcome,
                    resolve_price,
                    result_str,
                )

    def _get_position_outcome(self, condition_id: str) -> str:
        """Look up the outcome token for an open position.

        Args:
            condition_id: Market condition identifier.

        Returns:
            ``"Yes"`` or ``"No"`` based on the opening trade.

        """
        for trade in reversed(self._portfolio.trades):
            if trade.condition_id == condition_id and trade.side == Side.BUY:
                return trade.token_outcome
        return "Yes"

    def _build_result(self) -> PaperTradingResult:
        """Build the final result with computed metrics.

        Returns:
            Summary of the backtest run.

        """
        trades = self._portfolio.trades
        final_capital = self._portfolio.total_equity
        total_trades = len([t for t in trades if t.side == Side.BUY])
        metrics: dict[str, Decimal] = {
            "windows_processed": Decimal(self._windows_processed),
            "total_trades": Decimal(total_trades),
            "wins": Decimal(self._wins),
            "losses": Decimal(self._losses),
        }
        if self._wins + self._losses > 0:
            metrics["win_rate"] = Decimal(self._wins) / Decimal(
                self._wins + self._losses,
            )
        if total_trades > 0:
            total_return = final_capital - self._portfolio.capital
            # Calculate based on initial state
            metrics["total_return"] = total_return

        return PaperTradingResult(
            strategy_name=self._strategy.name,
            initial_capital=final_capital,  # placeholder, overridden by caller
            final_capital=final_capital,
            trades=tuple(trades),
            snapshots_processed=self._snapshots_processed,
            metrics=metrics,
        )


async def _fetch_all_candles(
    symbols: list[str],
    start_ts: int,
    end_ts: int,
) -> dict[str, list[Candle]]:
    """Fetch 1-minute candles from Binance for all symbols.

    Args:
        symbols: List of trading pair symbols (e.g. ``["BTC-USD"]``).
        start_ts: Start epoch seconds.
        end_ts: End epoch seconds.

    Returns:
        Mapping from symbol to sorted list of 1-minute candles.

    """
    result: dict[str, list[Candle]] = {}
    async with BinanceClient() as client:
        provider = BinanceCandleProvider(client)
        for symbol in symbols:
            typer.echo(f"Fetching {symbol} 1m candles...")
            candles = await provider.get_candles(
                symbol,
                Interval.M1,
                start_ts,
                end_ts,
            )
            result[symbol] = candles
            typer.echo(f"  Got {len(candles)} candles")
    return result


def _run_backtest(
    symbols: list[str],
    all_candles: dict[str, list[Candle]],
    start_ts: int,
    end_ts: int,
    *,
    capital: Decimal,
    snipe_threshold: Decimal,
    snipe_window: int,
    scale_factor: Decimal,
    kelly_frac: Decimal,
    max_position_pct: Decimal,
) -> PaperTradingResult:
    """Run the synchronous backtest replay.

    Args:
        symbols: Trading pair symbols.
        all_candles: Pre-fetched candle data per symbol.
        start_ts: Start epoch seconds.
        end_ts: End epoch seconds.
        capital: Initial virtual capital.
        snipe_threshold: Price threshold for the late snipe strategy.
        snipe_window: Seconds before market end to start sniping.
        scale_factor: Snapshot simulator price sensitivity.
        kelly_frac: Fractional Kelly multiplier.
        max_position_pct: Maximum fraction of capital per market.

    Returns:
        Summary of the backtest run.

    """
    strategy = PMLateSnipeStrategy(
        threshold=snipe_threshold,
        window_seconds=snipe_window,
    )
    portfolio = PaperPortfolio(capital, max_position_pct)
    simulator = SnapshotSimulator(scale_factor=scale_factor)

    runner = BacktestRunner(strategy, portfolio, simulator, kelly_frac)
    result = runner.replay(symbols, all_candles, start_ts, end_ts)

    # Fix initial_capital in result (runner doesn't have it)
    return PaperTradingResult(
        strategy_name=result.strategy_name,
        initial_capital=capital,
        final_capital=result.final_capital,
        trades=result.trades,
        snapshots_processed=result.snapshots_processed,
        metrics=result.metrics,
    )


def _display_result(result: PaperTradingResult) -> None:
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
        typer.echo(f"\nWindows processed: {result.metrics.get('windows_processed', 0)}")
        typer.echo(f"Total trades: {result.metrics.get('total_trades', 0)}")
        wins = result.metrics.get("wins", ZERO)
        losses = result.metrics.get("losses", ZERO)
        typer.echo(f"Wins: {wins}  Losses: {losses}")
        if "win_rate" in result.metrics:
            win_rate = result.metrics["win_rate"] * Decimal(100)
            typer.echo(f"Win rate: {win_rate:.1f}%")


def backtest_snipe(
    symbols: Annotated[
        str, typer.Option(help="Comma-separated symbols (e.g. BTC-USD,ETH-USD)")
    ] = _DEFAULT_SYMBOLS,
    start: Annotated[str, typer.Option(help="Start date YYYY-MM-DD")] = "",
    end: Annotated[str, typer.Option(help="End date YYYY-MM-DD")] = "",
    capital: Annotated[float, typer.Option(help="Initial virtual capital in USD")] = 1000.0,
    snipe_threshold: Annotated[
        float, typer.Option(help="Price threshold for late snipe (0.5-1.0)")
    ] = 0.8,
    snipe_window: Annotated[
        int, typer.Option(help="Seconds before market end to start sniping")
    ] = 90,
    scale_factor: Annotated[
        float, typer.Option(help="Snapshot simulator price sensitivity")
    ] = 15.0,
    kelly_frac: Annotated[float, typer.Option(help="Fractional Kelly multiplier")] = 0.25,
    max_position_pct: Annotated[
        float, typer.Option(help="Max fraction of capital per market")
    ] = 0.1,
    verbose: Annotated[  # noqa: FBT002
        bool, typer.Option("--verbose", "-v", help="Enable per-trade logging")
    ] = False,
) -> None:
    """Backtest the late snipe strategy on historical Binance data.

    Fetch 1-minute candles, simulate synthetic prediction market snapshots,
    and replay them through the late snipe strategy with Kelly sizing.
    No live Polymarket connection required.
    """
    if not start or not end:
        typer.echo("Error: --start and --end dates are required", err=True)
        raise typer.Exit(code=1)

    if verbose:
        _configure_verbose_logging()

    start_ts = _parse_date(start)
    end_ts = _parse_date(end)
    if start_ts >= end_ts:
        typer.echo("Error: --start must be before --end", err=True)
        raise typer.Exit(code=1)

    symbol_list = [s.strip() for s in symbols.split(",") if s.strip()]
    if not symbol_list:
        typer.echo("Error: at least one symbol is required", err=True)
        raise typer.Exit(code=1)

    typer.echo("Backtesting late snipe strategy")
    typer.echo(f"Symbols: {', '.join(symbol_list)}")
    typer.echo(f"Period: {start} to {end}")
    typer.echo(f"Threshold: {snipe_threshold}, Window: {snipe_window}s")
    typer.echo(f"Capital: ${capital}, Scale: {scale_factor}")
    typer.echo("")

    all_candles = asyncio.run(_fetch_all_candles(symbol_list, start_ts, end_ts))

    result = _run_backtest(
        symbol_list,
        all_candles,
        start_ts,
        end_ts,
        capital=Decimal(str(capital)),
        snipe_threshold=Decimal(str(snipe_threshold)),
        snipe_window=snipe_window,
        scale_factor=Decimal(str(scale_factor)),
        kelly_frac=Decimal(str(kelly_frac)),
        max_position_pct=Decimal(str(max_position_pct)),
    )

    _display_result(result)
