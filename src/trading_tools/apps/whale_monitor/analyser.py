"""Strategy analysis helpers for whale trade data.

Provide functions to analyse a whale's trading behaviour from stored trade
records: market type breakdown, side bias, hedging patterns, order sizing
statistics, timing analysis, and win/loss summaries. Designed for reuse
from the CLI ``whale-analyse`` command.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trading_tools.apps.whale_monitor.models import WhaleTrade

_PERCENTAGE_MULTIPLIER = 100
_MAX_TITLE_LENGTH = 50


def _empty_str_int_dict() -> dict[str, int]:
    """Return an empty dict[str, int] for dataclass default_factory."""
    return {}


def _empty_int_int_dict() -> dict[int, int]:
    """Return an empty dict[int, int] for dataclass default_factory."""
    return {}


def _empty_str_int_list() -> list[tuple[str, int]]:
    """Return an empty list[tuple[str, int]] for dataclass default_factory."""
    return []


@dataclass
class WhaleAnalysis:
    """Aggregated strategy analysis for a whale's trading activity.

    Attributes:
        address: Whale proxy wallet address.
        total_trades: Total number of trades in the analysis window.
        total_volume: Sum of ``size * price`` across all trades.
        buy_count: Number of BUY trades.
        sell_count: Number of SELL trades.
        avg_size: Mean token quantity per trade.
        avg_price: Mean execution price.
        unique_markets: Number of distinct markets (condition IDs) traded.
        market_breakdown: Trade counts per market title.
        outcome_breakdown: Trade counts per outcome label.
        side_breakdown: Trade counts per side (BUY/SELL).
        hourly_distribution: Trade counts per hour of day (0-23 UTC).
        top_markets: Top N markets by trade count.

    """

    address: str
    total_trades: int = 0
    total_volume: float = 0.0
    buy_count: int = 0
    sell_count: int = 0
    avg_size: float = 0.0
    avg_price: float = 0.0
    unique_markets: int = 0
    market_breakdown: dict[str, int] = field(default_factory=_empty_str_int_dict)
    outcome_breakdown: dict[str, int] = field(default_factory=_empty_str_int_dict)
    side_breakdown: dict[str, int] = field(default_factory=_empty_str_int_dict)
    hourly_distribution: dict[int, int] = field(default_factory=_empty_int_int_dict)
    top_markets: list[tuple[str, int]] = field(default_factory=_empty_str_int_list)


def analyse_trades(
    address: str,
    trades: list[WhaleTrade],
    *,
    top_n: int = 10,
) -> WhaleAnalysis:
    """Compute strategy analysis from a list of whale trades.

    Args:
        address: Whale proxy wallet address.
        trades: List of ``WhaleTrade`` records to analyse.
        top_n: Number of top markets to include in the summary.

    Returns:
        A ``WhaleAnalysis`` dataclass with aggregated statistics.

    """
    analysis = WhaleAnalysis(address=address)

    if not trades:
        return analysis

    analysis.total_trades = len(trades)

    total_size = 0.0
    total_price = 0.0
    market_counter: Counter[str] = Counter()
    outcome_counter: Counter[str] = Counter()
    side_counter: Counter[str] = Counter()
    hour_counter: Counter[int] = Counter()
    condition_ids: set[str] = set()

    for trade in trades:
        volume = trade.size * trade.price
        analysis.total_volume += volume
        total_size += trade.size
        total_price += trade.price

        if trade.side == "BUY":
            analysis.buy_count += 1
        else:
            analysis.sell_count += 1

        market_counter[trade.title] += 1
        outcome_counter[trade.outcome] += 1
        side_counter[trade.side] += 1
        condition_ids.add(trade.condition_id)

        hour = _epoch_seconds_to_hour(trade.timestamp)
        hour_counter[hour] += 1

    analysis.avg_size = total_size / len(trades)
    analysis.avg_price = total_price / len(trades)
    analysis.unique_markets = len(condition_ids)
    analysis.market_breakdown = dict(market_counter)
    analysis.outcome_breakdown = dict(outcome_counter)
    analysis.side_breakdown = dict(side_counter)
    analysis.hourly_distribution = dict(hour_counter)
    analysis.top_markets = market_counter.most_common(top_n)

    return analysis


def format_analysis(analysis: WhaleAnalysis) -> str:
    """Format a ``WhaleAnalysis`` as a human-readable report string.

    Args:
        analysis: Computed analysis to format.

    Returns:
        Multi-line formatted report string.

    """
    lines: list[str] = []
    lines.append(f"Whale Analysis: {analysis.address}")
    lines.append(f"{'=' * 60}")
    lines.append(f"Total trades: {analysis.total_trades}")
    lines.append(f"Total volume: ${analysis.total_volume:,.2f}")
    lines.append(f"Unique markets: {analysis.unique_markets}")
    lines.append("")

    lines.append("Side Breakdown:")
    buy_pct = (
        analysis.buy_count / analysis.total_trades * _PERCENTAGE_MULTIPLIER
        if analysis.total_trades
        else 0
    )
    sell_pct = (
        analysis.sell_count / analysis.total_trades * _PERCENTAGE_MULTIPLIER
        if analysis.total_trades
        else 0
    )
    lines.append(f"  BUY:  {analysis.buy_count} ({buy_pct:.1f}%)")
    lines.append(f"  SELL: {analysis.sell_count} ({sell_pct:.1f}%)")
    lines.append("")

    lines.append(f"Avg size: {analysis.avg_size:.2f} tokens")
    lines.append(f"Avg price: {analysis.avg_price:.4f}")
    lines.append("")

    if analysis.outcome_breakdown:
        lines.append("Outcome Breakdown:")
        for outcome, count in sorted(
            analysis.outcome_breakdown.items(), key=lambda x: x[1], reverse=True
        ):
            pct = count / analysis.total_trades * _PERCENTAGE_MULTIPLIER
            lines.append(f"  {outcome}: {count} ({pct:.1f}%)")
        lines.append("")

    if analysis.top_markets:
        lines.append(f"Top {len(analysis.top_markets)} Markets:")
        for title, count in analysis.top_markets:
            display_title = (
                title[:_MAX_TITLE_LENGTH] + "..." if len(title) > _MAX_TITLE_LENGTH else title
            )
            lines.append(f"  {display_title}: {count}")
        lines.append("")

    if analysis.hourly_distribution:
        lines.append("Hourly Distribution (UTC):")
        for hour in range(24):
            count = analysis.hourly_distribution.get(hour, 0)
            if count > 0:
                bar = "#" * min(count, 50)
                lines.append(f"  {hour:02d}:00  {bar} ({count})")

    return "\n".join(lines)


def _epoch_seconds_to_hour(epoch_seconds: int) -> int:
    """Convert epoch seconds to UTC hour of day (0-23).

    Args:
        epoch_seconds: Unix epoch timestamp in seconds.

    Returns:
        Hour of day in UTC (0-23).

    """
    _SECONDS_PER_DAY = 86400  # noqa: N806
    _SECONDS_PER_HOUR = 3600  # noqa: N806
    return (epoch_seconds % _SECONDS_PER_DAY) // _SECONDS_PER_HOUR
