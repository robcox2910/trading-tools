"""Strategy analysis helpers for whale trade data.

Provide functions to analyse a whale's trading behaviour from stored trade
records: market type breakdown, side bias, hedging patterns, order sizing
statistics, timing analysis, and win/loss summaries. Designed for reuse
from the CLI ``whale-analyse`` command.
"""

from __future__ import annotations

from collections import Counter, defaultdict
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


@dataclass
class MarketBreakdown:
    """Per-market directional breakdown of a whale's trading activity.

    Capture the volume, token size, and trade count for each side (Up/Down)
    within a single market, along with a bias ratio indicating how strongly
    the whale favours one side over the other.

    Attributes:
        condition_id: Market condition identifier.
        title: Human-readable market title.
        slug: Market URL slug.
        up_volume: Total dollar volume spent on Up (sum of size * price).
        down_volume: Total dollar volume spent on Down.
        up_size: Total tokens bought on Up.
        down_size: Total tokens bought on Down.
        trade_count: Total number of trades in this market.
        bias_ratio: Ratio of larger volume to smaller (e.g. 2.3 means 2.3:1).
        favoured_side: The side with more volume ("Up" or "Down").
        first_trade_ts: Epoch seconds of the earliest trade.
        last_trade_ts: Epoch seconds of the most recent trade.

    """

    condition_id: str
    title: str
    slug: str
    up_volume: float = 0.0
    down_volume: float = 0.0
    up_size: float = 0.0
    down_size: float = 0.0
    trade_count: int = 0
    bias_ratio: float = 1.0
    favoured_side: str = "Up"
    first_trade_ts: int = 0
    last_trade_ts: int = 0


_DEFAULT_MIN_TRADES = 10


def analyse_markets(
    trades: list[WhaleTrade],
    *,
    min_trades: int = _DEFAULT_MIN_TRADES,
) -> list[MarketBreakdown]:
    """Compute per-market directional breakdowns from whale trades.

    Group trades by ``condition_id``, separate Up vs Down by the ``outcome``
    field, and calculate volume, token size, trade count, bias ratio, and
    favoured side for each market.

    Args:
        trades: List of ``WhaleTrade`` records to analyse.
        min_trades: Minimum trades per market to include in results.

    Returns:
        List of ``MarketBreakdown`` sorted by ``last_trade_ts`` descending.

    """
    groups: dict[str, list[WhaleTrade]] = defaultdict(list)
    for trade in trades:
        groups[trade.condition_id].append(trade)

    results: list[MarketBreakdown] = []
    for condition_id, market_trades in groups.items():
        if len(market_trades) < min_trades:
            continue

        first = market_trades[0]
        breakdown = MarketBreakdown(
            condition_id=condition_id,
            title=first.title,
            slug=first.slug,
        )

        for trade in market_trades:
            volume = trade.size * trade.price
            if trade.outcome == "Up":
                breakdown.up_volume += volume
                breakdown.up_size += trade.size
            else:
                breakdown.down_volume += volume
                breakdown.down_size += trade.size

            breakdown.trade_count += 1

            ts = trade.timestamp
            if breakdown.first_trade_ts == 0 or ts < breakdown.first_trade_ts:
                breakdown.first_trade_ts = ts
            breakdown.last_trade_ts = max(breakdown.last_trade_ts, ts)

        larger = max(breakdown.up_volume, breakdown.down_volume)
        smaller = min(breakdown.up_volume, breakdown.down_volume)
        breakdown.bias_ratio = larger / smaller if smaller > 0 else larger if larger > 0 else 1.0
        breakdown.favoured_side = "Up" if breakdown.up_volume >= breakdown.down_volume else "Down"

        results.append(breakdown)

    results.sort(key=lambda m: m.last_trade_ts, reverse=True)
    return results


_MARKET_TABLE_HEADER = (
    f"{'Market':<50}  {'Up $':>9}  {'Down $':>9}  {'Bias':>7}  {'Fav':>4}  {'Trades':>6}"
)


def format_market_analysis(markets: list[MarketBreakdown]) -> str:
    """Format a list of market breakdowns as a human-readable report.

    Produce a table showing per-market directional bets followed by a
    summary of total markets, average bias, strongest bias, and overall
    side preference.

    Args:
        markets: List of ``MarketBreakdown`` to format.

    Returns:
        Multi-line formatted report string.

    """
    if not markets:
        return "No markets found matching the criteria."

    lines: list[str] = []
    lines.append("Per-Market Whale Analysis")
    lines.append(f"{'=' * 60}")
    lines.append("")
    lines.append(_MARKET_TABLE_HEADER)
    lines.append("-" * len(_MARKET_TABLE_HEADER))

    for m in markets:
        display_title = (
            m.title[:_MAX_TITLE_LENGTH] + "..." if len(m.title) > _MAX_TITLE_LENGTH else m.title
        )
        lines.append(
            f"{display_title:<50}  ${m.up_volume:>8.2f}  ${m.down_volume:>8.2f}"
            f"  {m.bias_ratio:>5.1f}:1  {m.favoured_side:>4}  {m.trade_count:>6}"
        )

    lines.append("")
    lines.append(f"{'=' * 60}")
    lines.append("Summary")
    lines.append(f"  Total markets: {len(markets)}")

    avg_bias = sum(m.bias_ratio for m in markets) / len(markets)
    lines.append(f"  Average bias ratio: {avg_bias:.1f}:1")

    strongest = max(markets, key=lambda m: m.bias_ratio)
    strongest_title = (
        strongest.title[:_MAX_TITLE_LENGTH] + "..."
        if len(strongest.title) > _MAX_TITLE_LENGTH
        else strongest.title
    )
    lines.append(f"  Strongest bias: {strongest.bias_ratio:.1f}:1 ({strongest_title})")

    up_favoured = sum(1 for m in markets if m.favoured_side == "Up")
    down_favoured = len(markets) - up_favoured
    lines.append(f"  Side preference: Up in {up_favoured}, Down in {down_favoured} markets")

    return "\n".join(lines)


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
