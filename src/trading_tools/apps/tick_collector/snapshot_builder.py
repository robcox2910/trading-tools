"""Build MarketSnapshots from raw tick data for backtesting.

Convert sequences of ``Tick`` records into bucketed ``MarketSnapshot`` objects
suitable for replay through trading strategies. Ticks are grouped by asset,
bucketed into configurable time intervals (default 1 second), and forward-filled
across gaps to produce a continuous price series. When pre-loaded order book
snapshots are provided, the builder enriches each market snapshot with real
depth data via nearest-timestamp matching.
"""

from __future__ import annotations

import bisect
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from trading_tools.apps.polymarket_bot.models import MarketSnapshot
from trading_tools.clients.polymarket.models import OrderBook, OrderLevel
from trading_tools.core.models import ZERO

if TYPE_CHECKING:
    from trading_tools.apps.tick_collector.models import OrderBookSnapshot, Tick

_DEFAULT_WINDOW_MINUTES = 5
_MS_PER_SECOND = 1000
_MS_PER_MINUTE = 60_000
_HALF = Decimal("0.5")


@dataclass(frozen=True)
class MarketWindow:
    """Metadata for a single prediction market window.

    Represent the time boundaries and resolution details for one window
    of tick data that maps to a single market lifecycle.

    Args:
        condition_id: Market condition identifier.
        start_ms: Window start in epoch milliseconds (aligned to window
            duration boundary).
        end_ms: Window end in epoch milliseconds
            (start + window_minutes * 60_000).
        end_date: ISO-8601 datetime string for the strategy's
            time-remaining calculation.

    """

    condition_id: str
    start_ms: int
    end_ms: int
    end_date: str


class SnapshotBuilder:
    """Convert raw ticks into bucketed MarketSnapshot sequences.

    Group ticks by asset_id, bucket them into fixed-length intervals,
    apply last-price-wins within each bucket, and forward-fill gaps.
    The lexicographically smaller asset_id is assigned as the YES token;
    the complement is derived as ``1 - price`` for the NO side.

    Args:
        bucket_seconds: Width of each time bucket in seconds. Default 1.
        window_minutes: Duration of each market window in minutes.
            Default 5 for 5-minute markets; set to 15 for 15-minute
            markets.

    """

    def __init__(
        self,
        bucket_seconds: int = 1,
        window_minutes: int = _DEFAULT_WINDOW_MINUTES,
    ) -> None:
        """Initialize the builder with a bucket width and window duration.

        Args:
            bucket_seconds: Seconds per snapshot bucket (must be >= 1).
            window_minutes: Duration of each market window in minutes.

        """
        self._bucket_seconds = bucket_seconds
        self._window_ms = window_minutes * _MS_PER_MINUTE

    def detect_window(self, condition_id: str, ticks: list[Tick]) -> MarketWindow:
        """Infer the market window from tick timestamps.

        Use the latest tick timestamp to find the window end by rounding up
        to the next window-duration boundary. This correctly handles markets
        where ticks arrive before the window opens (e.g. pre-market trading),
        ensuring the window covers the actual resolution period rather than
        the pre-market period.

        Args:
            condition_id: Market condition identifier for the window.
            ticks: Non-empty list of ticks within the window.

        Returns:
            A ``MarketWindow`` describing the inferred time boundaries.

        Raises:
            ValueError: If the ticks list is empty.

        """
        if not ticks:
            msg = "ticks list must not be empty"
            raise ValueError(msg)

        latest_ms = max(t.timestamp for t in ticks)
        end_ms = -(-latest_ms // self._window_ms) * self._window_ms
        start_ms = end_ms - self._window_ms
        end_date = datetime.fromtimestamp(
            end_ms / _MS_PER_SECOND,
            tz=UTC,
        ).isoformat()

        return MarketWindow(
            condition_id=condition_id,
            start_ms=start_ms,
            end_ms=end_ms,
            end_date=end_date,
        )

    def build_snapshots(
        self,
        ticks: list[Tick],
        window: MarketWindow,
        book_snapshots: dict[str, list[OrderBookSnapshot]] | None = None,
    ) -> list[MarketSnapshot]:
        """Build a time-bucketed sequence of MarketSnapshots from ticks.

        Assign the lexicographically smaller asset_id as YES; derive NO as
        ``1 - yes_price``. When two asset_ids are present, use the YES asset
        price directly. Bucket ticks into intervals of ``bucket_seconds``,
        apply last-price-wins within each bucket, and forward-fill gaps.

        When ``book_snapshots`` is provided and contains data for the YES
        asset, each bucket is enriched with the nearest order book depth
        snapshot via timestamp matching.

        Args:
            ticks: Non-empty list of ticks for a single condition_id.
            window: Market window metadata (start, end, end_date).
            book_snapshots: Optional mapping from token_id to a
                time-sorted list of ``OrderBookSnapshot`` records. When
                provided, the YES asset's books are used to populate the
                ``order_book`` field on each snapshot.

        Returns:
            List of ``MarketSnapshot`` objects, one per time bucket.

        Raises:
            ValueError: If the ticks list is empty.

        """
        if not ticks:
            msg = "ticks list must not be empty"
            raise ValueError(msg)

        # Identify YES asset (lexicographically smaller)
        asset_ids = sorted({t.asset_id for t in ticks})
        if not asset_ids:
            msg = "ticks contain no asset IDs"
            raise ValueError(msg)
        yes_asset = asset_ids[0]

        # Build per-bucket price map for the YES asset
        bucket_ms = self._bucket_seconds * _MS_PER_SECOND
        num_buckets = self._window_ms // bucket_ms

        bucket_prices: dict[int, Decimal] = {}
        for tick in ticks:
            if tick.asset_id == yes_asset:
                price = Decimal(str(tick.price))
            else:
                # NO asset: derive YES price as complement
                price = Decimal(1) - Decimal(str(tick.price))

            bucket_idx = (tick.timestamp - window.start_ms) // bucket_ms
            bucket_idx = max(0, min(bucket_idx, num_buckets - 1))
            bucket_prices[bucket_idx] = price

        # Pre-sort book snapshot timestamps for binary search
        yes_books = book_snapshots.get(yes_asset) if book_snapshots else None

        # Forward-fill to produce continuous series
        empty_book = OrderBook(
            token_id="",
            bids=(),
            asks=(),
            spread=ZERO,
            midpoint=_HALF,
        )
        snapshots: list[MarketSnapshot] = []
        last_price = _HALF  # default before first tick

        for i in range(num_buckets):
            if i in bucket_prices:
                last_price = bucket_prices[i]

            yes_price = last_price
            no_price = Decimal(1) - yes_price
            bucket_start_ms = window.start_ms + i * bucket_ms
            timestamp_seconds = bucket_start_ms // _MS_PER_SECOND

            order_book = empty_book
            if yes_books:
                matched = _find_nearest_book(yes_books, bucket_start_ms)
                if matched is not None:
                    order_book = matched

            snapshots.append(
                MarketSnapshot(
                    condition_id=window.condition_id,
                    question=f"Market {window.condition_id}",
                    timestamp=timestamp_seconds,
                    yes_price=yes_price,
                    no_price=no_price,
                    order_book=order_book,
                    volume=ZERO,
                    liquidity=ZERO,
                    end_date=window.end_date,
                ),
            )

        return snapshots


def _find_nearest_book(
    books: list[OrderBookSnapshot],
    target_ms: int,
) -> OrderBook | None:
    """Find the nearest order book snapshot and deserialize to ``OrderBook``.

    Use binary search on the pre-sorted list of snapshots to find the one
    closest to ``target_ms``. Return ``None`` if the list is empty.

    Args:
        books: Time-sorted list of ``OrderBookSnapshot`` records.
        target_ms: Target epoch milliseconds to match against.

    Returns:
        A deserialized ``OrderBook`` from the nearest snapshot, or ``None``
        if no snapshots are available.

    """
    if not books:
        return None

    timestamps = [b.timestamp for b in books]
    idx = bisect.bisect_left(timestamps, target_ms)

    # Compare candidates at idx-1 and idx
    if idx == 0:
        nearest = books[0]
    elif idx >= len(books):
        nearest = books[-1]
    else:
        before = books[idx - 1]
        after = books[idx]
        nearest = before if target_ms - before.timestamp <= after.timestamp - target_ms else after

    return _deserialize_book(nearest)


def _deserialize_book(snapshot: OrderBookSnapshot) -> OrderBook:
    """Convert an ``OrderBookSnapshot`` ORM record to a typed ``OrderBook``.

    Parse the JSON bid/ask arrays and construct ``OrderLevel`` tuples.

    Args:
        snapshot: Persisted order book snapshot with JSON level data.

    Returns:
        A typed ``OrderBook`` with deserialized price levels.

    """
    raw_bids: list[list[str]] = json.loads(snapshot.bids_json)
    raw_asks: list[list[str]] = json.loads(snapshot.asks_json)

    bids = tuple(OrderLevel(price=Decimal(b[0]), size=Decimal(b[1])) for b in raw_bids)
    asks = tuple(OrderLevel(price=Decimal(a[0]), size=Decimal(a[1])) for a in raw_asks)

    return OrderBook(
        token_id=snapshot.token_id,
        bids=bids,
        asks=asks,
        spread=Decimal(str(snapshot.spread)),
        midpoint=Decimal(str(snapshot.midpoint)),
    )
