"""Backtest limit order spread capture on historical order book snapshots.

Simulate placing resting limit buy orders on both Up and Down tokens of
5-minute crypto prediction markets.  Walk stored order book snapshots to
determine whether each limit order would fill (i.e. an ask exists at or
below our bid price), compute paired/unpaired P&L including Polymarket
fees, and aggregate results across a parameter grid.

The core insight: whales place cheap resting orders on **both** sides and
lock in guaranteed profit when ``bid_up + bid_down < 1.00``.  This module
backtests that strategy at various price points.
"""

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

import numpy as np

from trading_tools.apps.spread_capture.fees import compute_poly_fee
from trading_tools.apps.tick_collector.models import MarketMetadata, OrderBookSnapshot
from trading_tools.apps.tick_collector.repository import TickRepository
from trading_tools.clients.polymarket.models import OrderLevel
from trading_tools.core.models import ONE, ZERO, Candle

logger = logging.getLogger(__name__)

_MS_PER_SECOND = 1000
_HUNDRED = Decimal(100)


def _determine_outcome(candles: Sequence[Candle]) -> str | None:
    """Determine the winning side from candle data.

    Args:
        candles: 1-min candles spanning the market window.

    Returns:
        ``"Up"`` if close > open, ``"Down"`` if close < open, ``None`` if flat.

    """
    if not candles:
        return None
    open_price = candles[0].open
    close_price = candles[-1].close
    if close_price > open_price:
        return "Up"
    if close_price < open_price:
        return "Down"
    return None


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LimitBacktestConfig:
    """Configuration for a single limit order backtest run.

    Attributes:
        bid_price_up: Limit bid price for the Up token.
        bid_price_down: Limit bid price for the Down token.
        order_size: Token quantity to bid on each side.
        entry_delay_pct: Fraction of the window duration to wait before
            placing orders (0.0 = place at window open).
        fee_rate: Polymarket fee rate coefficient.
        fee_exponent: Exponent for the fee formula.

    """

    bid_price_up: Decimal
    bid_price_down: Decimal
    order_size: Decimal
    entry_delay_pct: Decimal = ZERO
    fee_rate: Decimal = Decimal("0.25")
    fee_exponent: int = 2


@dataclass(frozen=True)
class WindowFillResult:
    """Per-window outcome from simulating limit order fills.

    Attributes:
        condition_id: Market condition identifier.
        asset: Underlying asset (e.g. ``"BTC-USD"``).
        up_filled: Whether the Up limit order filled.
        down_filled: Whether the Down limit order filled.
        up_fill_qty: Actual filled quantity on Up (clamped by depth).
        down_fill_qty: Actual filled quantity on Down (clamped by depth).
        combined_cost: Per-token combined cost (``bid_up + bid_down``).
        paired_qty: Minimum of up and down filled quantities.
        guaranteed_pnl: Profit from paired tokens minus fees.
        pnl: Total P&L including directional on unpaired tokens.
        outcome: Winning side (``"Up"`` or ``"Down"``) or ``None``.

    """

    condition_id: str
    asset: str
    up_filled: bool
    down_filled: bool
    up_fill_qty: Decimal
    down_fill_qty: Decimal
    combined_cost: Decimal
    paired_qty: Decimal
    guaranteed_pnl: Decimal
    pnl: Decimal
    outcome: str | None


@dataclass(frozen=True)
class LimitBacktestResult:
    """Aggregate result for one configuration across all windows.

    Attributes:
        config: The configuration that produced these results.
        total_windows: Number of market windows evaluated.
        both_filled: Windows where both sides filled.
        up_only: Windows where only Up filled.
        down_only: Windows where only Down filled.
        neither_filled: Windows where neither side filled.
        fill_rate_both: Fraction of windows with both sides filled.
        avg_guaranteed_pnl: Mean guaranteed P&L per both-filled window.
        total_pnl: Sum of P&L across all windows.
        avg_pnl: Mean P&L per window (all windows).
        std_pnl: Standard deviation of per-window P&L.
        sharpe: ``avg_pnl / std_pnl`` (zero when ``std_pnl`` is zero).

    """

    config: LimitBacktestConfig
    total_windows: int
    both_filled: int
    up_only: int
    down_only: int
    neither_filled: int
    fill_rate_both: Decimal
    avg_guaranteed_pnl: Decimal
    total_pnl: Decimal
    avg_pnl: Decimal
    std_pnl: Decimal
    sharpe: Decimal


@dataclass(frozen=True)
class LimitGridResult:
    """Grid sweep output with per-cell results and axis values.

    Attributes:
        cells: Flat list of per-cell backtest results.
        bid_prices_up: Sorted Up bid prices searched.
        bid_prices_down: Sorted Down bid prices searched.
        order_sizes: Sorted order sizes searched.
        entry_delays: Sorted entry delay fractions searched.
        total_windows: Total market windows in the dataset.

    """

    cells: tuple[LimitBacktestResult, ...]
    bid_prices_up: tuple[Decimal, ...]
    bid_prices_down: tuple[Decimal, ...]
    order_sizes: tuple[Decimal, ...]
    entry_delays: tuple[Decimal, ...]
    total_windows: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_asks(snapshot: OrderBookSnapshot) -> tuple[OrderLevel, ...]:
    """Deserialise the asks from an order book snapshot.

    Args:
        snapshot: Stored snapshot with JSON-serialised ask levels.

    Returns:
        Tuple of ``OrderLevel`` objects sorted by price ascending.

    """
    asks_raw: list[list[str]] = json.loads(snapshot.asks_json)
    levels = tuple(OrderLevel(price=Decimal(a[0]), size=Decimal(a[1])) for a in asks_raw)
    return tuple(sorted(levels, key=lambda lv: lv.price))


def _available_qty_at_or_below(
    asks: tuple[OrderLevel, ...],
    bid_price: Decimal,
) -> Decimal:
    """Sum ask sizes at or below the bid price.

    Args:
        asks: Ask levels sorted by price ascending.
        bid_price: Our limit bid price.

    Returns:
        Total available quantity that would fill against our bid.

    """
    total = ZERO
    for level in asks:
        if level.price <= bid_price:
            total += level.size
        else:
            break
    return total


# ---------------------------------------------------------------------------
# Core algorithm
# ---------------------------------------------------------------------------


def _compute_directional_pnl(
    unpaired_qty: Decimal,
    bid_price: Decimal,
    fee: Decimal,
    side: str,
    outcome: str | None,
) -> Decimal:
    """Compute directional P&L for unpaired tokens on one side.

    Args:
        unpaired_qty: Tokens without a matching pair on the other side.
        bid_price: Price paid per token.
        fee: Per-token fee.
        side: Which side these tokens are on (``"Up"`` or ``"Down"``).
        outcome: Winning side or ``None`` if unknown.

    Returns:
        Directional P&L contribution (positive if won, negative if lost).

    """
    if unpaired_qty <= ZERO:
        return ZERO
    if outcome == side:
        return unpaired_qty * (ONE - bid_price) - unpaired_qty * fee
    # Lost or unknown outcome: lose cost basis + fees
    return -(unpaired_qty * bid_price + unpaired_qty * fee)


def simulate_limit_fills(
    config: LimitBacktestConfig,
    up_snapshots: Sequence[OrderBookSnapshot],
    down_snapshots: Sequence[OrderBookSnapshot],
    window_start_ts: int,
    window_end_ts: int,
    outcome: str | None,
) -> WindowFillResult:
    """Simulate limit order fills for one market window.

    Walk order book snapshots in time order after the entry delay.  A limit
    buy fills when the book shows asks at or below our bid price — i.e. a
    seller exists willing to trade at our resting price.

    Args:
        config: Limit backtest configuration.
        up_snapshots: Order book snapshots for the Up token, sorted by time.
        down_snapshots: Order book snapshots for the Down token, sorted by time.
        window_start_ts: Window start in epoch seconds.
        window_end_ts: Window end in epoch seconds.
        outcome: Winning side (``"Up"`` / ``"Down"``) or ``None``.

    Returns:
        A ``WindowFillResult`` describing what filled and the resulting P&L.

    """
    duration = window_end_ts - window_start_ts
    entry_ts_ms = int((window_start_ts + float(config.entry_delay_pct) * duration) * _MS_PER_SECOND)

    up_filled = False
    down_filled = False
    up_fill_qty = ZERO
    down_fill_qty = ZERO

    # Walk Up snapshots
    for snap in up_snapshots:
        if snap.timestamp < entry_ts_ms:
            continue
        asks = _parse_asks(snap)
        depth = _available_qty_at_or_below(asks, config.bid_price_up)
        if depth > ZERO:
            up_filled = True
            up_fill_qty = min(config.order_size, depth)
            break

    # Walk Down snapshots
    for snap in down_snapshots:
        if snap.timestamp < entry_ts_ms:
            continue
        asks = _parse_asks(snap)
        depth = _available_qty_at_or_below(asks, config.bid_price_down)
        if depth > ZERO:
            down_filled = True
            down_fill_qty = min(config.order_size, depth)
            break

    # Compute P&L
    combined_cost = config.bid_price_up + config.bid_price_down
    paired_qty = min(up_fill_qty, down_fill_qty)

    # Fees on each leg
    up_fee = compute_poly_fee(config.bid_price_up, config.fee_rate, config.fee_exponent)
    down_fee = compute_poly_fee(config.bid_price_down, config.fee_rate, config.fee_exponent)

    # Guaranteed P&L from paired tokens: each pair redeems for $1.00
    guaranteed_pnl = ZERO
    if paired_qty > ZERO:
        guaranteed_pnl = paired_qty * (ONE - combined_cost) - paired_qty * (up_fee + down_fee)

    # Directional P&L on unpaired tokens
    unpaired_up = up_fill_qty - paired_qty
    unpaired_down = down_fill_qty - paired_qty
    directional_pnl = _compute_directional_pnl(
        unpaired_up,
        config.bid_price_up,
        up_fee,
        "Up",
        outcome,
    ) + _compute_directional_pnl(
        unpaired_down,
        config.bid_price_down,
        down_fee,
        "Down",
        outcome,
    )

    pnl = guaranteed_pnl + directional_pnl

    return WindowFillResult(
        condition_id="",
        asset="",
        up_filled=up_filled,
        down_filled=down_filled,
        up_fill_qty=up_fill_qty,
        down_fill_qty=down_fill_qty,
        combined_cost=combined_cost,
        paired_qty=paired_qty,
        guaranteed_pnl=guaranteed_pnl,
        pnl=pnl,
        outcome=outcome,
    )


# ---------------------------------------------------------------------------
# Single-config runner
# ---------------------------------------------------------------------------


async def run_limit_backtest(
    config: LimitBacktestConfig,
    repo: TickRepository,
    start_ts: int,
    end_ts: int,
    *,
    series_slug: str | None = None,
    all_snapshots: list[OrderBookSnapshot] | None = None,
    candles_by_asset: dict[str, list[Candle]] | None = None,
) -> LimitBacktestResult:
    """Run a limit order backtest for a single configuration.

    Load market metadata and order book snapshots, simulate limit fills
    for each window, and aggregate the results.

    Args:
        config: Limit backtest configuration.
        repo: Tick repository for loading historical data.
        start_ts: Start epoch seconds (inclusive).
        end_ts: End epoch seconds (inclusive).
        series_slug: Filter metadata to a specific series slug.
        all_snapshots: Pre-fetched snapshots to avoid redundant DB queries
            during grid sweeps.  When ``None``, snapshots are loaded per window.
        candles_by_asset: Pre-loaded Binance candles keyed by asset for
            outcome determination.

    Returns:
        Aggregate backtest result with fill rates and P&L metrics.

    """
    metadata_list = await repo.get_market_metadata_in_range(
        start_ts, end_ts, series_slug=series_slug
    )

    if not metadata_list:
        logger.warning("No market metadata found for %d - %d", start_ts, end_ts)
        return _empty_result(config)

    # Index pre-fetched snapshots by token_id
    snaps_by_token: dict[str, list[OrderBookSnapshot]] = {}
    if all_snapshots is not None:
        for snap in all_snapshots:
            snaps_by_token.setdefault(snap.token_id, []).append(snap)

    candles = candles_by_asset or {}
    window_results = await _evaluate_windows(config, repo, metadata_list, snaps_by_token, candles)

    return _aggregate_results(config, window_results, len(metadata_list))


async def _evaluate_windows(
    config: LimitBacktestConfig,
    repo: TickRepository,
    metadata_list: list[MarketMetadata],
    snaps_by_token: dict[str, list[OrderBookSnapshot]],
    candles: dict[str, list[Candle]],
) -> list[WindowFillResult]:
    """Evaluate limit fills across all market windows.

    Args:
        config: Limit backtest configuration.
        repo: Tick repository (used only when ``snaps_by_token`` is empty).
        metadata_list: Market metadata for each window.
        snaps_by_token: Pre-indexed snapshots by token ID.
        candles: Binance candles keyed by asset.

    Returns:
        List of per-window fill results.

    """
    window_results: list[WindowFillResult] = []

    for meta in metadata_list:
        start_ms = meta.window_start_ts * _MS_PER_SECOND
        end_ms = meta.window_end_ts * _MS_PER_SECOND

        # Load or filter snapshots for this window
        up_snaps = _get_window_snapshots(snaps_by_token, meta.up_token_id, start_ms, end_ms)
        down_snaps = _get_window_snapshots(snaps_by_token, meta.down_token_id, start_ms, end_ms)

        if not up_snaps and not down_snaps and not snaps_by_token:
            # Fetch from DB when not pre-loaded
            up_snaps = await repo.get_order_book_snapshots_in_range(
                meta.up_token_id, start_ms, end_ms
            )
            down_snaps = await repo.get_order_book_snapshots_in_range(
                meta.down_token_id, start_ms, end_ms
            )

        # Determine outcome
        asset_candles: list[Candle] = candles.get(meta.asset, [])
        window_candles = [
            c for c in asset_candles if meta.window_start_ts <= c.timestamp <= meta.window_end_ts
        ]
        outcome = _determine_outcome(window_candles)

        result = simulate_limit_fills(
            config,
            up_snaps,
            down_snaps,
            meta.window_start_ts,
            meta.window_end_ts,
            outcome,
        )

        # Patch in market identifiers
        result = WindowFillResult(
            condition_id=meta.condition_id,
            asset=meta.asset,
            up_filled=result.up_filled,
            down_filled=result.down_filled,
            up_fill_qty=result.up_fill_qty,
            down_fill_qty=result.down_fill_qty,
            combined_cost=result.combined_cost,
            paired_qty=result.paired_qty,
            guaranteed_pnl=result.guaranteed_pnl,
            pnl=result.pnl,
            outcome=result.outcome,
        )

        logger.info(
            "WINDOW %s up=%s down=%s paired=%.1f pnl=%.4f outcome=%s",
            meta.condition_id[:8],
            result.up_filled,
            result.down_filled,
            result.paired_qty,
            result.pnl,
            outcome,
        )

        window_results.append(result)

    return window_results


def _get_window_snapshots(
    snaps_by_token: dict[str, list[OrderBookSnapshot]],
    token_id: str,
    start_ms: int,
    end_ms: int,
) -> list[OrderBookSnapshot]:
    """Filter pre-loaded snapshots to a specific window.

    Args:
        snaps_by_token: All snapshots indexed by token ID.
        token_id: Token to filter for.
        start_ms: Window start in epoch milliseconds.
        end_ms: Window end in epoch milliseconds.

    Returns:
        Snapshots within the time range, sorted by timestamp.

    """
    all_snaps = snaps_by_token.get(token_id, [])
    filtered = [s for s in all_snaps if start_ms <= s.timestamp <= end_ms]
    return sorted(filtered, key=lambda s: s.timestamp)


def _aggregate_results(
    config: LimitBacktestConfig,
    window_results: list[WindowFillResult],
    total_windows: int,
) -> LimitBacktestResult:
    """Aggregate per-window results into a single backtest result.

    Args:
        config: The configuration used.
        window_results: Per-window fill outcomes.
        total_windows: Total number of windows evaluated.

    Returns:
        Aggregated ``LimitBacktestResult``.

    """
    if not window_results:
        return _empty_result(config)

    both_filled = sum(1 for r in window_results if r.up_filled and r.down_filled)
    up_only = sum(1 for r in window_results if r.up_filled and not r.down_filled)
    down_only = sum(1 for r in window_results if r.down_filled and not r.up_filled)
    neither = sum(1 for r in window_results if not r.up_filled and not r.down_filled)

    fill_rate_both = Decimal(both_filled) / Decimal(total_windows) if total_windows > 0 else ZERO

    # Guaranteed P&L only for both-filled windows
    guaranteed_pnls = [r.guaranteed_pnl for r in window_results if r.up_filled and r.down_filled]
    avg_guaranteed_pnl = (
        sum(guaranteed_pnls, start=ZERO) / Decimal(len(guaranteed_pnls))
        if guaranteed_pnls
        else ZERO
    )

    pnl_values = [float(r.pnl) for r in window_results]
    total_pnl: Decimal = sum((r.pnl for r in window_results), start=ZERO)
    avg_pnl = total_pnl / Decimal(total_windows) if total_windows > 0 else ZERO

    std_pnl = Decimal(str(np.std(pnl_values))) if pnl_values else ZERO
    sharpe = avg_pnl / std_pnl if std_pnl > ZERO else ZERO

    return LimitBacktestResult(
        config=config,
        total_windows=total_windows,
        both_filled=both_filled,
        up_only=up_only,
        down_only=down_only,
        neither_filled=neither,
        fill_rate_both=fill_rate_both,
        avg_guaranteed_pnl=avg_guaranteed_pnl,
        total_pnl=total_pnl,
        avg_pnl=avg_pnl,
        std_pnl=std_pnl,
        sharpe=sharpe,
    )


def _empty_result(config: LimitBacktestConfig) -> LimitBacktestResult:
    """Return an empty result when no data is available.

    Args:
        config: The configuration used.

    Returns:
        A ``LimitBacktestResult`` with all zeros.

    """
    return LimitBacktestResult(
        config=config,
        total_windows=0,
        both_filled=0,
        up_only=0,
        down_only=0,
        neither_filled=0,
        fill_rate_both=ZERO,
        avg_guaranteed_pnl=ZERO,
        total_pnl=ZERO,
        avg_pnl=ZERO,
        std_pnl=ZERO,
        sharpe=ZERO,
    )


# ---------------------------------------------------------------------------
# Grid sweep
# ---------------------------------------------------------------------------


async def run_limit_grid(
    repo: TickRepository,
    start_ts: int,
    end_ts: int,
    bid_prices_up: list[Decimal],
    bid_prices_down: list[Decimal],
    order_sizes: list[Decimal],
    entry_delays: list[Decimal],
    *,
    series_slug: str | None = None,
    candles_by_asset: dict[str, list[Candle]] | None = None,
) -> LimitGridResult:
    """Run a grid search over limit order parameters.

    Pre-fetch all order book snapshots once, then iterate every parameter
    combination without additional DB queries.

    Args:
        repo: Tick repository for loading historical data.
        start_ts: Start epoch seconds.
        end_ts: End epoch seconds.
        bid_prices_up: List of Up bid prices to sweep.
        bid_prices_down: List of Down bid prices to sweep.
        order_sizes: List of order sizes to sweep.
        entry_delays: List of entry delay fractions to sweep.
        series_slug: Filter to a specific series slug.
        candles_by_asset: Pre-loaded Binance candles keyed by asset.

    Returns:
        A ``LimitGridResult`` with all cell results and axis values.

    """
    # Pre-fetch all snapshots once
    start_ms = start_ts * _MS_PER_SECOND
    end_ms = end_ts * _MS_PER_SECOND
    all_snapshots = await repo.get_all_book_snapshots_in_range(start_ms, end_ms)
    logger.info("Pre-fetched %d order book snapshots", len(all_snapshots))

    sorted_up = sorted(bid_prices_up)
    sorted_down = sorted(bid_prices_down)
    sorted_sizes = sorted(order_sizes)
    sorted_delays = sorted(entry_delays)

    total_combos = len(sorted_up) * len(sorted_down) * len(sorted_sizes) * len(sorted_delays)
    logger.info("Running %d grid combinations...", total_combos)

    cells: list[LimitBacktestResult] = []
    cell_num = 0

    for bu in sorted_up:
        for bd in sorted_down:
            for size in sorted_sizes:
                for delay in sorted_delays:
                    cell_num += 1
                    config = LimitBacktestConfig(
                        bid_price_up=bu,
                        bid_price_down=bd,
                        order_size=size,
                        entry_delay_pct=delay,
                    )
                    result = await run_limit_backtest(
                        config,
                        repo,
                        start_ts,
                        end_ts,
                        series_slug=series_slug,
                        all_snapshots=all_snapshots,
                        candles_by_asset=candles_by_asset,
                    )
                    cells.append(result)

                    logger.info(
                        "[%d/%d] up=%.2f down=%.2f size=%.0f delay=%.0f%% "
                        "→ fill=%.1f%% pnl=%.4f sharpe=%.3f",
                        cell_num,
                        total_combos,
                        bu,
                        bd,
                        size,
                        delay * _HUNDRED,
                        result.fill_rate_both * _HUNDRED,
                        result.total_pnl,
                        result.sharpe,
                    )

    total_windows = cells[0].total_windows if cells else 0

    return LimitGridResult(
        cells=tuple(cells),
        bid_prices_up=tuple(sorted_up),
        bid_prices_down=tuple(sorted_down),
        order_sizes=tuple(sorted_sizes),
        entry_delays=tuple(sorted_delays),
        total_windows=total_windows,
    )


# ---------------------------------------------------------------------------
# Table formatting
# ---------------------------------------------------------------------------


def format_limit_grid_table(
    result: LimitGridResult,
    metric: str = "total_pnl",
) -> str:
    """Format grid results as a bid_up x bid_down markdown table.

    For each (bid_up, bid_down) cell, pick the best order_size and
    entry_delay combination (by total P&L) and display the requested
    metric.

    Args:
        result: Completed grid search result.
        metric: Which ``LimitBacktestResult`` field to display.  Supported
            values: ``"total_pnl"``, ``"fill_rate_both"``, ``"sharpe"``,
            ``"avg_guaranteed_pnl"``.

    Returns:
        A markdown-formatted table string.

    """
    # Group cells by (bid_up, bid_down), keeping the best by total_pnl
    best_cells: dict[tuple[Decimal, Decimal], LimitBacktestResult] = {}
    for cell in result.cells:
        key = (cell.config.bid_price_up, cell.config.bid_price_down)
        existing = best_cells.get(key)
        if existing is None or cell.total_pnl > existing.total_pnl:
            best_cells[key] = cell

    # Header
    header_parts = ["| Bid Up \\ Down"]
    header_parts.extend(f" {bd:.2f}" for bd in result.bid_prices_down)
    header = " |".join(header_parts) + " |"

    sep_parts = ["|---"]
    sep_parts.extend("---" for _ in result.bid_prices_down)
    sep = "|".join(sep_parts) + "|"

    # Data rows
    rows: list[str] = []
    for bu in result.bid_prices_up:
        parts = [f"| {bu:.2f}"]
        for bd in result.bid_prices_down:
            cell = best_cells.get((bu, bd))
            if cell is None:
                parts.append(" -")
            else:
                value = getattr(cell, metric)
                if metric == "fill_rate_both":
                    parts.append(f" {value * _HUNDRED:.1f}%")
                elif metric == "sharpe":
                    parts.append(f" {value:.3f}")
                elif metric in ("total_pnl", "avg_pnl", "avg_guaranteed_pnl"):
                    parts.append(f" ${value:.2f}")
                else:
                    parts.append(f" {value}")
        rows.append(" |".join(parts) + " |")

    return "\n".join([header, sep, *rows])
