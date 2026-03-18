"""Strategy analysis helpers for whale trade data.

Provide functions to analyse a whale's trading behaviour from stored trade
records: market type breakdown, side bias, hedging patterns, order sizing
statistics, timing analysis, and win/loss summaries. Designed for reuse
from the CLI ``whale-analyse`` command.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Union

import pandas as pd
import pandera.pandas as pa
from pandera.typing import Series  # noqa: TC002 — needed at runtime by pandera DataFrameModel

from trading_tools.apps.whale_monitor.enricher import EnrichedTrade

if TYPE_CHECKING:
    from collections.abc import Sequence

    from trading_tools.apps.whale_monitor.models import WhaleTrade

# Union type accepted by public analysis functions.
TradeInput = Union["WhaleTrade", EnrichedTrade]

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


def _unwrap(trade: WhaleTrade | EnrichedTrade) -> WhaleTrade:
    """Return the underlying ``WhaleTrade`` from either a plain or enriched trade.

    Args:
        trade: A ``WhaleTrade`` instance or an ``EnrichedTrade`` wrapper.

    Returns:
        The ``WhaleTrade`` instance.

    """
    if isinstance(trade, EnrichedTrade):
        return trade.trade  # type: ignore[return-value]
    return trade  # type: ignore[return-value]


def trades_to_df(trades: Sequence[WhaleTrade | EnrichedTrade]) -> pd.DataFrame:
    """Convert a list of trade records to a DataFrame.

    Accept both plain ``WhaleTrade`` instances and ``EnrichedTrade`` wrappers —
    the latter are unwrapped transparently.  A ``notional`` column (``size *
    price``) and an ``hour`` column (UTC hour of day, 0-23) are derived
    and appended for convenience.

    Args:
        trades: ``WhaleTrade`` or ``EnrichedTrade`` instances to convert.

    Returns:
        DataFrame with one row per trade and the following columns:
        ``condition_id``, ``title``, ``slug``, ``side``, ``outcome``,
        ``size``, ``price``, ``notional``, ``timestamp``, ``hour``,
        ``close_ts`` (epoch seconds float when the market has resolved,
        ``NaN`` otherwise).

    """
    if not trades:
        return pd.DataFrame(
            columns=[
                "condition_id",
                "title",
                "slug",
                "side",
                "outcome",
                "size",
                "price",
                "notional",
                "timestamp",
                "close_ts",
                "hour",
            ]
        )

    _SECONDS_PER_HOUR = 3600  # noqa: N806

    rows: list[dict[str, object]] = []
    for t in trades:
        w = _unwrap(t)
        if isinstance(t, EnrichedTrade) and t.close_datetime is not None:
            close_ts: float = t.close_datetime.timestamp()
        else:
            close_ts = float("nan")
        rows.append(
            {
                "condition_id": w.condition_id,
                "title": w.title,
                "slug": w.slug,
                "side": w.side,
                "outcome": w.outcome,
                "size": float(w.size),
                "price": float(w.price),
                "timestamp": int(w.timestamp),
                "close_ts": close_ts,
            }
        )
    df = pd.DataFrame(rows)

    df["notional"] = df["size"] * df["price"]
    df["hour"] = (df["timestamp"] % _SECONDS_PER_DAY) // _SECONDS_PER_HOUR
    return df


# ── TradeSummary ──────────────────────────────────────────────────────────────


@dataclass
class TradeSummary:
    """Scalar summary statistics for a whale's trading activity.

    A lightweight complement to ``WhaleAnalysis`` containing only per-wallet
    aggregate metrics, without breakdown dicts or top-market lists.  Use
    ``to_series()`` to convert a record into a ``pandas.Series`` for easy
    DataFrame assembly across multiple wallets.

    Attributes:
        address: Whale proxy wallet address.
        total_trades: Total number of trades in the analysis window.
        total_volume: Sum of ``size * price`` across all trades.
        buy_count: Number of BUY trades.
        sell_count: Number of SELL trades.
        avg_size: Mean token quantity per trade.
        avg_buy_price: Mean execution price across BUY trades.
        avg_sell_price: Mean execution price across SELL trades.
        unique_markets: Number of distinct markets (condition IDs) traded.
        avg_trade_duration_d: Mean days between trade placement and market
            resolution, across trades with a known resolution time. ``None``
            when no resolution times are available.
        min_trade_duration_d: Shortest such duration in days. ``None`` when
            unavailable.
        max_trade_duration_d: Longest such duration in days. ``None`` when
            unavailable.
        single_side_market_pct: Percentage of markets (0-100) where the trader
            only placed BUY trades or only SELL trades, with no mixing of sides.
        average_daily_trades_1w: Mean trades per day over the 7 days ending at
            ``as_of`` (as supplied to ``summarize_trades``).
        average_daily_trades_1m: Mean trades per day over the 30 days ending at
            ``as_of``.
        sharpe_ratio: Per-trade Sharpe ratio (mean P&L / std P&L, risk-free
            rate of zero) computed from resolved ``EnrichedTrade`` instances
            whose markets have a known winner.  ``None`` when fewer than two
            resolved trades are available or when no enriched trades are
            supplied.

    """

    address: str
    total_trades: int = 0
    total_volume: float = 0.0
    buy_count: int = 0
    sell_count: int = 0
    avg_size: float = 0.0
    avg_buy_price: float = 0.0
    avg_sell_price: float = 0.0
    unique_markets: int = 0
    avg_trade_duration_d: float | None = None
    min_trade_duration_d: float | None = None
    max_trade_duration_d: float | None = None
    single_side_market_pct: float = 0.0
    average_daily_trades_1w: float = 0.0
    average_daily_trades_1m: float = 0.0
    sharpe_ratio: float | None = None

    def to_series(self) -> pd.Series:  # type: ignore[type-arg]
        """Convert this summary to a ``pandas.Series``.

        The series index matches the field names, making it straightforward to
        build a multi-wallet summary DataFrame::

            pd.DataFrame([s.to_series() for s in summaries])

        Returns:
            A ``pandas.Series`` with one entry per field.

        """
        return pd.Series(asdict(self))


_DAYS_1W = 7
_DAYS_1M = 30
_SECONDS_PER_DAY = 86400
_MIN_SHARPE_OBSERVATIONS = 2


def _sharpe_from_pnls(pnls: list[float]) -> float | None:
    """Compute the per-trade Sharpe ratio from a list of resolved P&L values.

    Use a risk-free rate of zero (standard for alternative assets).  Return
    ``None`` when fewer than ``_MIN_SHARPE_OBSERVATIONS`` values are
    available, since standard deviation is undefined on a single observation.

    Args:
        pnls: List of per-trade P&L values from resolved, completed trades.

    Returns:
        Sharpe ratio (mean / std, ddof=1), or ``None`` when insufficient data.

    """
    if len(pnls) < _MIN_SHARPE_OBSERVATIONS:
        return None
    series = pd.Series(pnls, dtype=float)
    std = float(series.std(ddof=1))
    if std == 0.0:
        return None
    return round(float(series.mean()) / std, 4)


def summarize_trades(
    address: str,
    trades: Sequence[WhaleTrade | EnrichedTrade],
    *,
    as_of: datetime | None = None,
) -> TradeSummary:
    """Compute scalar summary statistics from a list of whale trades.

    Accept both plain ``WhaleTrade`` and ``EnrichedTrade`` wrappers.

    Args:
        address: Whale proxy wallet address.
        trades: List of ``WhaleTrade`` or ``EnrichedTrade`` records to summarise.
        as_of: Reference datetime for rolling-window calculations.  Defaults to
            ``datetime.now(UTC)`` when ``None``.  Must be timezone-aware.

    Returns:
        A ``TradeSummary`` dataclass with aggregated scalar statistics,
        including ``average_daily_trades_1w`` and ``average_daily_trades_1m``
        computed over the 7- and 30-day windows ending at ``as_of``.

    """
    df = trades_to_df(trades)

    if df.empty:
        return TradeSummary(address=address)

    reference = as_of if as_of is not None else datetime.now(UTC)
    ref_ts = int(reference.timestamp())

    cutoff_1w = ref_ts - _DAYS_1W * _SECONDS_PER_DAY
    cutoff_1m = ref_ts - _DAYS_1M * _SECONDS_PER_DAY

    avg_daily_1w = df[df["timestamp"] >= cutoff_1w].shape[0] / _DAYS_1W
    avg_daily_1m = df[df["timestamp"] >= cutoff_1m].shape[0] / _DAYS_1M

    buys = df[df["side"] == "BUY"]
    sells = df[df["side"] == "SELL"]
    side_counts = df["side"].value_counts().to_dict()

    resolved_pnls = [
        t.trade_pnl
        for t in trades
        if isinstance(t, EnrichedTrade) and t.trade_pnl is not None and not t.is_active
    ]
    sharpe = _sharpe_from_pnls(resolved_pnls)

    # Trade duration: only valid where close_ts is known (EnrichedTrade with resolved market)
    seconds_per_day = 86400.0
    durations = (df["close_ts"] - df["timestamp"]) / seconds_per_day
    valid_durations = durations.dropna()
    valid_durations = valid_durations[valid_durations >= 0]

    avg_dur: float | None = (
        round(float(valid_durations.mean()), 4) if not valid_durations.empty else None
    )
    min_dur: float | None = (
        round(float(valid_durations.min()), 4) if not valid_durations.empty else None
    )
    max_dur: float | None = (
        round(float(valid_durations.max()), 4) if not valid_durations.empty else None
    )

    # Single-side market %: markets where all trades are the same side
    sides_per_market = df.groupby("condition_id")["side"].nunique()
    n_markets = len(sides_per_market)
    n_single_side = int((sides_per_market == 1).sum())
    single_side_pct = round(n_single_side / n_markets * 100, 1) if n_markets > 0 else 0.0

    return TradeSummary(
        address=address,
        total_trades=len(df),
        total_volume=float(df["notional"].sum()),
        buy_count=int(side_counts.get("BUY", 0)),
        sell_count=int(side_counts.get("SELL", 0)),
        avg_size=float(df["size"].mean()),
        avg_buy_price=float(buys["price"].mean()) if not buys.empty else 0.0,
        avg_sell_price=float(sells["price"].mean()) if not sells.empty else 0.0,
        unique_markets=int(df["condition_id"].nunique()),
        avg_trade_duration_d=avg_dur,
        min_trade_duration_d=min_dur,
        max_trade_duration_d=max_dur,
        single_side_market_pct=single_side_pct,
        average_daily_trades_1w=round(avg_daily_1w, 2),
        average_daily_trades_1m=round(avg_daily_1m, 2),
        sharpe_ratio=sharpe,
    )


# ── Breakdown functions ───────────────────────────────────────────────────────


def market_breakdown(df: pd.DataFrame) -> pd.DataFrame:
    """Compute per-market trade counts from a trades DataFrame.

    Args:
        df: Trades DataFrame as returned by ``trades_to_df()``.

    Returns:
        DataFrame with columns ``title`` and ``count``, sorted by ``count``
        descending.

    """
    counts = df["title"].value_counts().reset_index()
    counts.columns = pd.Index(["title", "count"])
    return counts


def outcome_breakdown(df: pd.DataFrame) -> pd.DataFrame:
    """Compute per-outcome trade counts from a trades DataFrame.

    Args:
        df: Trades DataFrame as returned by ``trades_to_df()``.

    Returns:
        DataFrame with columns ``outcome`` and ``count``, sorted by ``count``
        descending.

    """
    counts = df["outcome"].value_counts().reset_index()
    counts.columns = pd.Index(["outcome", "count"])
    return counts


def side_breakdown(df: pd.DataFrame) -> pd.DataFrame:
    """Compute per-side (BUY/SELL) trade counts from a trades DataFrame.

    Args:
        df: Trades DataFrame as returned by ``trades_to_df()``.

    Returns:
        DataFrame with columns ``side`` and ``count``, sorted by ``count``
        descending.

    """
    counts = df["side"].value_counts().reset_index()
    counts.columns = pd.Index(["side", "count"])
    return counts


def hourly_distribution(df: pd.DataFrame) -> pd.DataFrame:
    """Compute trade counts by hour of day from a trades DataFrame.

    Args:
        df: Trades DataFrame as returned by ``trades_to_df()``.

    Returns:
        DataFrame with columns ``hour`` (0-23 UTC) and ``count``, sorted by
        ``hour`` ascending.

    """
    counts = df["hour"].value_counts().reset_index()
    counts.columns = pd.Index(["hour", "count"])
    return counts.sort_values("hour").reset_index(drop=True)


# ── Market position summary ───────────────────────────────────────────────────


@dataclass
class MarketPositionSummary:
    """Aggregated position metrics for one trader in one market.

    Summarise all trades for a single whale in a single named market,
    capturing net position, realised cash flows, and (where available) P&L
    from market resolution.

    Attributes:
        username: Display name or address of the trader.
        market_name: The market title used to filter trades.
        total_trades: Total number of trades in this market.
        buy_count: Number of BUY trades.
        sell_count: Number of SELL trades.
        total_bought: Total tokens purchased (sum of ``size`` for BUYs).
        total_sold: Total tokens sold (sum of ``size`` for SELLs).
        net_position: Remaining open token position (``total_bought -
            total_sold``).  Positive means a net long position.
        total_cost: Total USDC spent on BUYs (sum of ``size * price`` for
            BUYs).
        total_realized: Total USDC received from SELLs (sum of ``size *
            price`` for SELLs).
        avg_buy_price: Volume-weighted average price across BUY trades.
        avg_sell_price: Volume-weighted average price across SELL trades.
        total_pnl: Sum of per-trade P&L values from resolved
            ``EnrichedTrade`` instances, or ``None`` when the market has
            not yet resolved or plain ``WhaleTrade`` records are supplied.
        is_resolved: ``True`` when the market is known to have a winner
            (requires enriched trades).  ``None`` when resolution status
            is unavailable.

    """

    username: str
    market_name: str
    total_trades: int = 0
    buy_count: int = 0
    sell_count: int = 0
    total_bought: float = 0.0
    total_sold: float = 0.0
    net_position: float = 0.0
    total_cost: float = 0.0
    total_realized: float = 0.0
    avg_buy_price: float = 0.0
    avg_sell_price: float = 0.0
    total_pnl: float | None = None
    is_resolved: bool | None = None


def market_position_summary(
    username: str,
    trades: Sequence[WhaleTrade | EnrichedTrade],
    market_name: str,
) -> tuple[pd.DataFrame, MarketPositionSummary]:
    """Return all trades for a named market and an aggregated position summary.

    Filter ``trades`` to those whose ``title`` contains ``market_name``
    (case-insensitive), sort them by timestamp ascending, and compute
    position-level metrics.

    P&L is populated only when ``EnrichedTrade`` instances with a resolved
    ``winning_outcome`` are present in the filtered set.

    Args:
        username: Display name or address of the trader, used to label the
            summary.
        trades: Full trade list for the trader — plain ``WhaleTrade`` or
            ``EnrichedTrade`` wrappers accepted.
        market_name: Substring to match against trade titles (case-insensitive).
            Use the full market title for an exact match, or a shorter prefix
            for a fuzzy filter.

    Returns:
        A 2-tuple ``(df, summary)`` where ``df`` is a ``pandas.DataFrame``
        of matching trades sorted by ``timestamp`` ascending, and ``summary``
        is a ``MarketPositionSummary`` with aggregated position metrics.

    """
    needle = market_name.lower()
    filtered: list[WhaleTrade | EnrichedTrade] = [
        t for t in trades if needle in _unwrap(t).title.lower()
    ]

    if not filtered:
        return pd.DataFrame(), MarketPositionSummary(username=username, market_name=market_name)

    df = trades_to_df(filtered).sort_values("timestamp").reset_index(drop=True)

    buys = df[df["side"] == "BUY"]
    sells = df[df["side"] == "SELL"]

    total_bought = float(buys["size"].sum()) if not buys.empty else 0.0
    total_sold = float(sells["size"].sum()) if not sells.empty else 0.0
    total_cost = float(buys["notional"].sum()) if not buys.empty else 0.0
    total_realized = float(sells["notional"].sum()) if not sells.empty else 0.0

    avg_buy_price = float(buys["price"].mean()) if not buys.empty else 0.0
    avg_sell_price = float(sells["price"].mean()) if not sells.empty else 0.0

    # P&L and resolution status from enriched trades
    enriched_filtered = [t for t in filtered if isinstance(t, EnrichedTrade)]
    is_resolved: bool | None = None
    total_pnl: float | None = None

    if enriched_filtered:
        is_resolved = not enriched_filtered[0].is_active
        pnls = [t.trade_pnl for t in enriched_filtered if t.trade_pnl is not None]
        if pnls:
            total_pnl = round(sum(pnls), 4)

    return df, MarketPositionSummary(
        username=username,
        market_name=market_name,
        total_trades=len(df),
        buy_count=len(buys),
        sell_count=len(sells),
        total_bought=round(total_bought, 4),
        total_sold=round(total_sold, 4),
        net_position=round(total_bought - total_sold, 4),
        total_cost=round(total_cost, 4),
        total_realized=round(total_realized, 4),
        avg_buy_price=round(avg_buy_price, 4),
        avg_sell_price=round(avg_sell_price, 4),
        total_pnl=total_pnl,
        is_resolved=is_resolved,
    )


# ── WhaleAnalysis (full legacy analysis) ─────────────────────────────────────


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
        avg_buy_price: Mean execution price across BUY trades.
        avg_sell_price: Mean execution price across SELL trades.
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
    avg_buy_price: float = 0.0
    avg_sell_price: float = 0.0
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


class MarketBreakdownSchema(pa.DataFrameModel):
    """Pandera schema for the DataFrame returned by ``analyse_markets()``.

    Each row represents one market the whale traded in.  Columns mirror the
    fields of the former ``MarketBreakdown`` dataclass.

    Columns:
        condition_id: Market condition identifier (hex string).
        title: Human-readable market title.
        slug: Market URL slug.
        up_volume: Total dollar volume spent on Up outcomes (size * price).
        down_volume: Total dollar volume spent on Down outcomes.
        up_size: Total tokens bought on Up.
        down_size: Total tokens bought on Down.
        trade_count: Total number of trades in this market.
        bias_ratio: Ratio of larger side volume to smaller (>= 1.0).
        favoured_side: The side with more volume -- ``"Up"`` or ``"Down"``.
        first_trade_ts: Epoch seconds of the earliest trade in this market.
        last_trade_ts: Epoch seconds of the most recent trade, used as the
            default sort key (descending).
        avg_duration_d: Mean days between trade placement and market
            resolution for this market. NaN when no resolution times are
            available.
        min_duration_d: Minimum duration in days. NaN when unavailable.
        max_duration_d: Maximum duration in days. NaN when unavailable.
        side_bias: ``"BUY_ONLY"`` when all trades were BUYs, ``"SELL_ONLY"``
            when all were SELLs, ``"MIXED"`` when both sides appear.

    """

    condition_id: Series[str]
    title: Series[str]
    slug: Series[str]
    up_volume: Series[float] = pa.Field(ge=0.0)
    down_volume: Series[float] = pa.Field(ge=0.0)
    up_size: Series[float] = pa.Field(ge=0.0)
    down_size: Series[float] = pa.Field(ge=0.0)
    trade_count: Series[int] = pa.Field(ge=0)
    bias_ratio: Series[float] = pa.Field(ge=1.0)
    favoured_side: Series[str] = pa.Field(isin=["Up", "Down"])
    first_trade_ts: Series[int] = pa.Field(ge=0)
    last_trade_ts: Series[int] = pa.Field(ge=0)
    avg_duration_d: Series[float] = pa.Field(nullable=True)
    min_duration_d: Series[float] = pa.Field(nullable=True)
    max_duration_d: Series[float] = pa.Field(nullable=True)
    side_bias: Series[str] = pa.Field(isin=["BUY_ONLY", "SELL_ONLY", "MIXED"])

    class Config:  # type: ignore[misc]
        """Pandera model configuration."""

        coerce = True


def analyse_markets(
    trades: Sequence[WhaleTrade | EnrichedTrade],
    *,
    min_trades: int = _DEFAULT_MIN_TRADES,
) -> pd.DataFrame:
    """Compute per-market directional breakdowns from whale trades.

    Group trades by ``condition_id``, separate Up vs Down by the ``outcome``
    field, and calculate volume, token size, trade count, bias ratio, and
    favoured side for each market.  Accept both plain ``WhaleTrade`` and
    ``EnrichedTrade`` wrappers.

    The returned DataFrame conforms to ``MarketBreakdownSchema`` — call
    ``MarketBreakdownSchema.validate(df)`` to re-verify after any manual
    mutation.

    Args:
        trades: List of ``WhaleTrade`` or ``EnrichedTrade`` records to analyse.
        min_trades: Minimum trades per market to include in results.

    Returns:
        ``MarketBreakdownSchema``-validated DataFrame with one row per market,
        sorted by ``last_trade_ts`` descending.

    """
    df = trades_to_df(trades)
    if df.empty:
        return pd.DataFrame(columns=list(MarketBreakdownSchema.to_schema().columns))

    # Per-market summary aggregates
    mkt = df.groupby("condition_id").agg(
        title=("title", "first"),
        slug=("slug", "first"),
        trade_count=("notional", "count"),
        first_trade_ts=("timestamp", "min"),
        last_trade_ts=("timestamp", "max"),
    )
    mkt = mkt[mkt["trade_count"] >= min_trades]  # type: ignore[operator]

    # Directional volume and size — pivot Up / Down separately to avoid unstack
    up_df = (
        df[df["outcome"] == "Up"]
        .groupby("condition_id")[["notional", "size"]]
        .sum()
        .rename(columns={"notional": "up_notional", "size": "up_size"})
    )
    down_df = (
        df[df["outcome"] == "Down"]
        .groupby("condition_id")[["notional", "size"]]
        .sum()
        .rename(columns={"notional": "down_notional", "size": "down_size"})
    )

    combined = mkt.join(up_df, how="left").join(down_df, how="left").fillna(0.0)
    combined = combined.reset_index()

    # Pre-compute per-market duration and side stats from the raw trades df
    mkt_groups = df.groupby("condition_id")

    rows: list[dict[str, object]] = []
    for _, row in combined.iterrows():
        up_vol = float(row["up_notional"])
        down_vol = float(row["down_notional"])
        larger = max(up_vol, down_vol)
        smaller = min(up_vol, down_vol)
        bias_ratio = larger / smaller if smaller > 0 else (larger if larger > 0 else 1.0)

        cid = str(row["condition_id"])
        mkt_df = mkt_groups.get_group(cid) if cid in mkt_groups.groups else pd.DataFrame()

        # Duration stats (in days)
        spd = 86400.0
        raw_dur: pd.Series[float] = (  # type: ignore[type-arg]
            ((mkt_df["close_ts"] - mkt_df["timestamp"]) / spd).dropna().astype(float)  # type: ignore[assignment]
            if not mkt_df.empty
            else pd.Series([], dtype=float)
        )
        dur_series: pd.Series[float] = raw_dur[raw_dur >= 0]  # type: ignore[type-arg,index]
        avg_dur_mkt = float(dur_series.mean()) if not dur_series.empty else float("nan")  # type: ignore[arg-type]
        min_dur_mkt = float(dur_series.min()) if not dur_series.empty else float("nan")  # type: ignore[arg-type]
        max_dur_mkt = float(dur_series.max()) if not dur_series.empty else float("nan")  # type: ignore[arg-type]

        # Side bias
        n_sides = mkt_df["side"].nunique() if not mkt_df.empty else 0
        if n_sides == 0:
            side_bias = "MIXED"
        elif n_sides == 1:
            only_side = str(mkt_df["side"].iloc[0])
            side_bias = "BUY_ONLY" if only_side == "BUY" else "SELL_ONLY"
        else:
            side_bias = "MIXED"

        rows.append(
            {
                "condition_id": cid,
                "title": str(row["title"]),
                "slug": str(row["slug"]),
                "up_volume": up_vol,
                "down_volume": down_vol,
                "up_size": float(row["up_size"]),
                "down_size": float(row["down_size"]),
                "trade_count": int(row["trade_count"]),
                "bias_ratio": bias_ratio,
                "favoured_side": "Up" if up_vol >= down_vol else "Down",
                "first_trade_ts": int(row["first_trade_ts"]),
                "last_trade_ts": int(row["last_trade_ts"]),
                "avg_duration_d": avg_dur_mkt,
                "min_duration_d": min_dur_mkt,
                "max_duration_d": max_dur_mkt,
                "side_bias": side_bias,
            }
        )

    if not rows:
        return pd.DataFrame(columns=list(MarketBreakdownSchema.to_schema().columns))

    result = pd.DataFrame(rows).sort_values("last_trade_ts", ascending=False).reset_index(drop=True)
    return MarketBreakdownSchema.validate(result)  # type: ignore[return-value]


_MARKET_TABLE_HEADER = (
    f"{'Market':<50}  {'Up $':>9}  {'Down $':>9}  {'Bias':>7}  {'Fav':>4}  {'Trades':>6}"
)


def format_market_analysis(markets: pd.DataFrame) -> str:
    """Format a market breakdown DataFrame as a human-readable report.

    Produce a table showing per-market directional bets followed by a
    summary of total markets, average bias, strongest bias, and overall
    side preference.

    Args:
        markets: ``MarketBreakdownSchema``-validated DataFrame as returned by
            ``analyse_markets()``.

    Returns:
        Multi-line formatted report string.

    """
    if markets.empty:
        return "No markets found matching the criteria."

    lines: list[str] = []
    lines.append("Per-Market Whale Analysis")
    lines.append(f"{'=' * 60}")
    lines.append("")
    lines.append(_MARKET_TABLE_HEADER)
    lines.append("-" * len(_MARKET_TABLE_HEADER))

    for _, row in markets.iterrows():
        title = str(row["title"])
        display_title = (
            title[:_MAX_TITLE_LENGTH] + "..." if len(title) > _MAX_TITLE_LENGTH else title
        )
        lines.append(
            f"{display_title:<50}  ${row['up_volume']:>8.2f}  ${row['down_volume']:>8.2f}"
            f"  {row['bias_ratio']:>5.1f}:1  {row['favoured_side']:>4}  {row['trade_count']:>6}"
        )

    lines.append("")
    lines.append(f"{'=' * 60}")
    lines.append("Summary")
    lines.append(f"  Total markets: {len(markets)}")

    avg_bias = float(markets["bias_ratio"].mean())
    lines.append(f"  Average bias ratio: {avg_bias:.1f}:1")

    strongest = markets.loc[markets["bias_ratio"].idxmax()]
    strongest_title = str(strongest["title"])
    strongest_title = (
        strongest_title[:_MAX_TITLE_LENGTH] + "..."
        if len(strongest_title) > _MAX_TITLE_LENGTH
        else strongest_title
    )
    strongest_bias = float(strongest["bias_ratio"])  # type: ignore[arg-type]
    lines.append(f"  Strongest bias: {strongest_bias:.1f}:1 ({strongest_title})")

    up_favoured = int((markets["favoured_side"] == "Up").sum())
    down_favoured = len(markets) - up_favoured
    lines.append(f"  Side preference: Up in {up_favoured}, Down in {down_favoured} markets")

    return "\n".join(lines)


def analyse_trades(
    address: str,
    trades: Sequence[WhaleTrade | EnrichedTrade],
    *,
    top_n: int = 10,
) -> WhaleAnalysis:
    """Compute strategy analysis from a list of whale trades.

    Accept both plain ``WhaleTrade`` and ``EnrichedTrade`` wrappers.

    Args:
        address: Whale proxy wallet address.
        trades: List of ``WhaleTrade`` or ``EnrichedTrade`` records to analyse.
        top_n: Number of top markets to include in the summary.

    Returns:
        A ``WhaleAnalysis`` dataclass with aggregated statistics.

    """
    df = trades_to_df(trades)

    if df.empty:
        return WhaleAnalysis(address=address)

    buys = df[df["side"] == "BUY"]
    sells = df[df["side"] == "SELL"]

    side_counts = df["side"].value_counts().to_dict()
    top_markets_series = df["title"].value_counts().head(top_n)

    return WhaleAnalysis(
        address=address,
        total_trades=len(df),
        total_volume=float(df["notional"].sum()),
        buy_count=int(side_counts.get("BUY", 0)),
        sell_count=int(side_counts.get("SELL", 0)),
        avg_size=float(df["size"].mean()),
        avg_buy_price=float(buys["price"].mean()) if not buys.empty else 0.0,
        avg_sell_price=float(sells["price"].mean()) if not sells.empty else 0.0,
        unique_markets=int(df["condition_id"].nunique()),
        market_breakdown={str(k): int(v) for k, v in df["title"].value_counts().items()},
        outcome_breakdown={str(k): int(v) for k, v in df["outcome"].value_counts().items()},
        side_breakdown={str(k): int(v) for k, v in side_counts.items()},
        hourly_distribution={int(h): int(c) for h, c in df["hour"].value_counts().items()},  # type: ignore[arg-type]
        top_markets=[(str(k), int(v)) for k, v in top_markets_series.items()],
    )


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
    lines.append(f"Avg buy price:  {analysis.avg_buy_price:.4f}")
    lines.append(f"Avg sell price: {analysis.avg_sell_price:.4f}")
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
