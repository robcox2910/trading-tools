"""Correlate whale directional bets with actual spot price movement.

Parse asset and time window from Polymarket market titles, fetch spot
candles from Binance, and determine whether the whale's favoured side
(Up/Down) matched the actual price direction during that window.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Protocol
from zoneinfo import ZoneInfo

from trading_tools.core.models import HUNDRED, ZERO, Interval

if TYPE_CHECKING:
    from trading_tools.apps.whale_monitor.analyser import MarketBreakdown
    from trading_tools.core.models import Candle

_ET = ZoneInfo("America/New_York")
_UTC = ZoneInfo("UTC")

_PERCENTAGE_MULTIPLIER = 100
_MAX_TITLE_LENGTH = 50

_FLAT_THRESHOLD = Decimal("0.001")
_NOON = 12

# Regex patterns for time windows in market titles
_RANGE_PATTERN = re.compile(
    r"(\d{1,2})(?::(\d{2}))?(AM|PM)\s*-\s*(\d{1,2})(?::(\d{2}))?(AM|PM)\s+ET",
    re.IGNORECASE,
)
_SINGLE_PATTERN = re.compile(
    r"(\d{1,2})(AM|PM)\s+ET",
    re.IGNORECASE,
)

# Date pattern to extract the market date from the title
_DATE_PATTERN = re.compile(
    r"(January|February|March|April|May|June|July|August|September|October|November|December"
    r"|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2})",
    re.IGNORECASE,
)

_MONTH_MAP: dict[str, int] = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

_SECONDS_PER_HOUR = 3600


class CandleProvider(Protocol):
    """Protocol for fetching candle data from a market data provider."""

    async def get_candles(
        self,
        symbol: str,
        interval: Interval,
        start_ts: int,
        end_ts: int,
    ) -> list[Candle]:
        """Fetch candles for the given symbol and time range."""
        ...


@dataclass(frozen=True)
class SpotCorrelation:
    """Spot price movement data correlated with a whale's directional bet.

    Capture the actual price movement during a market's time window,
    including direction, volatility, and whether the whale's favoured
    side matched reality.

    Attributes:
        symbol: Trading pair (e.g. ``"BTC-USD"``).
        window_start_ts: UTC epoch seconds of the window start.
        window_end_ts: UTC epoch seconds of the window end.
        open_price: Opening price at window start.
        close_price: Closing price at window end.
        high_price: Highest price during the window.
        low_price: Lowest price during the window.
        price_change_pct: Percentage change from open to close.
        volatility_pct: Percentage range (high - low) relative to open.
        actual_direction: ``"Up"``, ``"Down"``, or ``"Flat"``.
        whale_correct: Whether the whale's favoured side matched actual direction.

    """

    symbol: str
    window_start_ts: int
    window_end_ts: int
    open_price: Decimal
    close_price: Decimal
    high_price: Decimal
    low_price: Decimal
    price_change_pct: Decimal
    volatility_pct: Decimal
    actual_direction: str
    whale_correct: bool


@dataclass(frozen=True)
class CorrelatedMarket:
    """Pair a market breakdown with its optional spot price correlation.

    When the market title cannot be parsed for asset or time window,
    ``correlation`` is ``None``.

    Attributes:
        breakdown: The per-market directional breakdown.
        correlation: Spot price correlation data, or ``None`` if unparseable.

    """

    breakdown: MarketBreakdown
    correlation: SpotCorrelation | None


_ASSET_PREFIX_MAP: dict[str, str] = {
    "bitcoin": "BTC-USD",
    "ethereum": "ETH-USD",
    "solana": "SOL-USD",
    "xrp": "XRP-USD",
    "dogecoin": "DOGE-USD",
}


def parse_asset(title: str) -> str | None:
    """Extract the spot asset symbol from a market title.

    Match the first word of the title against known crypto asset names
    to determine the corresponding Binance trading pair.

    Args:
        title: Market title string (e.g. ``"Bitcoin Up or Down - Mar 13, 6PM ET"``).

    Returns:
        Trading pair like ``"BTC-USD"`` or ``None`` if unrecognised.

    """
    lower = title.lower()
    for prefix, pair in _ASSET_PREFIX_MAP.items():
        if lower.startswith(prefix):
            return pair
    return None


def parse_time_window(title: str, first_trade_ts: int) -> tuple[int, int] | None:
    """Extract the time window as UTC epoch seconds from a market title.

    Support two patterns:
    - Range: ``"6:30PM-6:45PM ET"`` → (start, end)
    - Single hourly: ``"6PM ET"`` → (start, start + 1 hour)

    The date is extracted from the title (e.g. ``"March 13"``). If no date
    is found, the date of ``first_trade_ts`` in ET is used as fallback.

    Args:
        title: Market title containing the time window.
        first_trade_ts: Epoch seconds of the earliest trade, used as
            fallback for the date.

    Returns:
        A ``(start_ts, end_ts)`` tuple of UTC epoch seconds, or ``None``
        if no time pattern is matched.

    """
    market_date = _parse_date_from_title(title, first_trade_ts)

    range_match = _RANGE_PATTERN.search(title)
    if range_match:
        start_time = _parse_time(
            int(range_match.group(1)),
            int(range_match.group(2) or 0),
            range_match.group(3),
        )
        end_time = _parse_time(
            int(range_match.group(4)),
            int(range_match.group(5) or 0),
            range_match.group(6),
        )
        start_dt = datetime.combine(market_date, start_time, tzinfo=_ET)
        end_dt = datetime.combine(market_date, end_time, tzinfo=_ET)
        return (int(start_dt.timestamp()), int(end_dt.timestamp()))

    single_match = _SINGLE_PATTERN.search(title)
    if single_match:
        hour = int(single_match.group(1))
        ampm = single_match.group(2)
        start_time = _parse_time(hour, 0, ampm)
        start_dt = datetime.combine(market_date, start_time, tzinfo=_ET)
        end_dt = start_dt + timedelta(hours=1)
        return (int(start_dt.timestamp()), int(end_dt.timestamp()))

    return None


def _parse_date_from_title(title: str, fallback_ts: int) -> datetime:
    """Extract a date from the title or fall back to the trade timestamp.

    Args:
        title: Market title that may contain a date like ``"March 13"``.
        fallback_ts: Epoch seconds used if no date is found in the title.

    Returns:
        A timezone-naive date (representing a calendar day in ET).

    """
    date_match = _DATE_PATTERN.search(title)
    if date_match:
        month_str = date_match.group(1).lower()
        day = int(date_match.group(2))
        month = _MONTH_MAP.get(month_str)
        if month is not None:
            # Infer year from the fallback timestamp
            fallback_dt = datetime.fromtimestamp(fallback_ts, tz=_ET)
            return datetime(fallback_dt.year, month, day, tzinfo=_ET)

    fallback_dt = datetime.fromtimestamp(fallback_ts, tz=_ET)
    return datetime(fallback_dt.year, fallback_dt.month, fallback_dt.day, tzinfo=_ET)


def _parse_time(hour: int, minute: int, ampm: str) -> time:
    """Convert 12-hour time components to a ``time`` object.

    Args:
        hour: Hour in 12-hour format (1-12).
        minute: Minute (0-59).
        ampm: ``"AM"`` or ``"PM"`` (case-insensitive).

    Returns:
        A ``time`` object in 24-hour format.

    """
    if ampm.upper() == "AM" and hour == _NOON:
        hour = 0
    elif ampm.upper() == "PM" and hour != _NOON:
        hour += _NOON
    return time(hour, minute)


def compute_correlation(
    breakdown: MarketBreakdown,
    candles: list[Candle],
) -> SpotCorrelation:
    """Compute spot price correlation for a market breakdown.

    Calculate price movement statistics from the candles and determine
    whether the whale's favoured side matches the actual price direction.

    Args:
        breakdown: Market breakdown with the whale's favoured side.
        candles: List of candles covering the market's time window.

    Returns:
        A ``SpotCorrelation`` with price statistics and correctness.

    Raises:
        ValueError: If candles list is empty.

    """
    if not candles:
        msg = "Cannot compute correlation with empty candles"
        raise ValueError(msg)

    open_price = candles[0].open
    close_price = candles[-1].close
    high_price = max(c.high for c in candles)
    low_price = min(c.low for c in candles)

    price_change_pct = (close_price - open_price) / open_price * HUNDRED
    volatility_pct = (high_price - low_price) / open_price * HUNDRED

    if abs(price_change_pct) < _FLAT_THRESHOLD:
        actual_direction = "Flat"
    elif price_change_pct > ZERO:
        actual_direction = "Up"
    else:
        actual_direction = "Down"

    whale_correct = breakdown.favoured_side == actual_direction

    symbol = parse_asset(breakdown.title) or "UNKNOWN"

    return SpotCorrelation(
        symbol=symbol,
        window_start_ts=candles[0].timestamp,
        window_end_ts=candles[-1].timestamp,
        open_price=open_price,
        close_price=close_price,
        high_price=high_price,
        low_price=low_price,
        price_change_pct=price_change_pct,
        volatility_pct=volatility_pct,
        actual_direction=actual_direction,
        whale_correct=whale_correct,
    )


async def correlate_markets(
    breakdowns: list[MarketBreakdown],
    candle_provider: CandleProvider,
) -> list[CorrelatedMarket]:
    """Correlate whale market breakdowns with spot price data.

    For each breakdown, parse the asset and time window from the title.
    If both are found, fetch 1-minute candles and compute correlation.
    Markets that can't be parsed get ``correlation=None``.

    Args:
        breakdowns: List of per-market breakdowns from ``analyse_markets()``.
        candle_provider: Provider for fetching candle data (e.g. Binance).

    Returns:
        List of ``CorrelatedMarket`` in the same order as input.

    """
    results: list[CorrelatedMarket] = []

    for breakdown in breakdowns:
        asset = parse_asset(breakdown.title)
        window = parse_time_window(breakdown.title, breakdown.first_trade_ts)

        if asset is None or window is None:
            results.append(CorrelatedMarket(breakdown=breakdown, correlation=None))
            continue

        start_ts, end_ts = window
        candles = await candle_provider.get_candles(asset, Interval.M1, start_ts, end_ts)

        if not candles:
            results.append(CorrelatedMarket(breakdown=breakdown, correlation=None))
            continue

        correlation = compute_correlation(breakdown, candles)
        results.append(CorrelatedMarket(breakdown=breakdown, correlation=correlation))

    return results


def format_correlated_analysis(markets: list[CorrelatedMarket]) -> str:
    """Format correlated market data as a human-readable report.

    Produce a table showing each market's favoured side, actual price
    direction, correctness, price change, volatility, and bias ratio,
    followed by an accuracy summary.

    Args:
        markets: List of ``CorrelatedMarket`` to format.

    Returns:
        Multi-line formatted report string.

    """
    if not markets:
        return "No markets found matching the criteria."

    correlated = [m for m in markets if m.correlation is not None]
    skipped = len(markets) - len(correlated)

    lines: list[str] = []
    lines.append("Whale Spot Price Correlation")
    lines.append(f"{'=' * 60}")
    lines.append("")

    header = (
        f"{'Market':<50}  {'Fav':>4}  {'Actual':>6}  {'OK':>2}"
        f"  {'Chg%':>7}  {'Vol%':>6}  {'Bias':>7}"
    )
    lines.append(header)
    lines.append("-" * len(header))

    for m in markets:
        b = m.breakdown
        display_title = (
            b.title[:_MAX_TITLE_LENGTH] + "..." if len(b.title) > _MAX_TITLE_LENGTH else b.title
        )

        if m.correlation is not None:
            c = m.correlation
            ok_str = "Y" if c.whale_correct else "N"
            lines.append(
                f"{display_title:<50}  {b.favoured_side:>4}  {c.actual_direction:>6}"
                f"  {ok_str:>2}  {c.price_change_pct:>+6.2f}%  {c.volatility_pct:>5.2f}%"
                f"  {b.bias_ratio:>5.1f}:1"
            )
        else:
            lines.append(f"{display_title:<50}  {b.favoured_side:>4}  {'—':>6}  {'—':>2}")

    lines.append("")
    lines.append(f"{'=' * 60}")
    lines.append("Summary")

    total_correlated = len(correlated)
    lines.append(f"  Markets correlated: {total_correlated} ({skipped} skipped — no price data)")

    if total_correlated > 0:
        correct = [
            m for m in correlated if m.correlation is not None and m.correlation.whale_correct
        ]
        incorrect = [
            m for m in correlated if m.correlation is not None and not m.correlation.whale_correct
        ]
        correct_count = len(correct)
        pct = correct_count / total_correlated * _PERCENTAGE_MULTIPLIER
        lines.append(f"  Correct predictions: {correct_count} / {total_correlated} ({pct:.1f}%)")

        if correct:
            avg_correct = sum(
                m.correlation.price_change_pct for m in correct if m.correlation is not None
            ) / len(correct)
            lines.append(f"  Avg price change when correct: {avg_correct:+.2f}%")

        if incorrect:
            avg_incorrect = sum(
                m.correlation.price_change_pct for m in incorrect if m.correlation is not None
            ) / len(incorrect)
            lines.append(f"  Avg price change when incorrect: {avg_incorrect:+.2f}%")

        avg_vol = (
            sum(m.correlation.volatility_pct for m in correlated if m.correlation is not None)
            / total_correlated
        )
        lines.append(f"  Avg volatility: {avg_vol:.2f}%")

    return "\n".join(lines)
