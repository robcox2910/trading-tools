"""Build MarketSnapshots from raw tick data for backtesting.

Convert sequences of ``Tick`` records into bucketed ``MarketSnapshot`` objects
suitable for replay through the late snipe strategy. Ticks are grouped by
asset, bucketed into configurable time intervals (default 1 second), and
forward-filled across gaps to produce a continuous price series.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from trading_tools.apps.polymarket_bot.models import MarketSnapshot
from trading_tools.apps.tick_collector.models import Tick
from trading_tools.clients.polymarket.models import OrderBook
from trading_tools.core.models import ZERO

_FIVE_MINUTES_MS = 300_000
_MS_PER_SECOND = 1000
_HALF = Decimal("0.5")


@dataclass(frozen=True)
class MarketWindow:
    """Metadata for a single 5-minute prediction market window.

    Represent the time boundaries and resolution details for one window
    of tick data that maps to a single market lifecycle.

    Args:
        condition_id: Market condition identifier.
        start_ms: Window start in epoch milliseconds (5-minute aligned).
        end_ms: Window end in epoch milliseconds (start + 300,000).
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

    """

    def __init__(self, bucket_seconds: int = 1) -> None:
        """Initialize the builder with a bucket width.

        Args:
            bucket_seconds: Seconds per snapshot bucket (must be >= 1).

        """
        self._bucket_seconds = bucket_seconds

    def detect_window(self, condition_id: str, ticks: list[Tick]) -> MarketWindow:
        """Infer the 5-minute market window from tick timestamps.

        Round the earliest tick timestamp down to the nearest 5-minute
        epoch-millisecond boundary. The window end is start + 300,000 ms.

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

        earliest_ms = min(t.timestamp for t in ticks)
        start_ms = (earliest_ms // _FIVE_MINUTES_MS) * _FIVE_MINUTES_MS
        end_ms = start_ms + _FIVE_MINUTES_MS
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
    ) -> list[MarketSnapshot]:
        """Build a time-bucketed sequence of MarketSnapshots from ticks.

        Assign the lexicographically smaller asset_id as YES; derive NO as
        ``1 - yes_price``. When two asset_ids are present, use the YES asset
        price directly. Bucket ticks into intervals of ``bucket_seconds``,
        apply last-price-wins within each bucket, and forward-fill gaps.

        Args:
            ticks: Non-empty list of ticks for a single condition_id.
            window: Market window metadata (start, end, end_date).

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
        yes_asset = asset_ids[0]

        # Build per-bucket price map for the YES asset
        bucket_ms = self._bucket_seconds * _MS_PER_SECOND
        num_buckets = _FIVE_MINUTES_MS // bucket_ms

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

            snapshots.append(
                MarketSnapshot(
                    condition_id=window.condition_id,
                    question=f"Market {window.condition_id}",
                    timestamp=timestamp_seconds,
                    yes_price=yes_price,
                    no_price=no_price,
                    order_book=empty_book,
                    volume=ZERO,
                    liquidity=ZERO,
                    end_date=window.end_date,
                ),
            )

        return snapshots
