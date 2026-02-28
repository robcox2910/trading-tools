"""Tests for the snapshot builder that converts ticks to MarketSnapshots."""

from decimal import Decimal

import pytest

from trading_tools.apps.tick_collector.models import Tick
from trading_tools.apps.tick_collector.snapshot_builder import (
    MarketWindow,
    SnapshotBuilder,
)

_CONDITION_ID = "cond_abc123"
_ASSET_YES = "asset_aaa"
_ASSET_NO = "asset_zzz"
_FEE_BPS = 200
_FIVE_MINUTES_MS = 300_000
_WINDOW_START_MS = 1_700_000_100_000
_WINDOW_ALIGNED_MS = 1_700_000_100_000 - (1_700_000_100_000 % _FIVE_MINUTES_MS)
_BUCKET_SECONDS = 1
_EXPECTED_BUCKETS_300 = 300


def _make_tick(
    asset_id: str = _ASSET_YES,
    condition_id: str = _CONDITION_ID,
    timestamp: int = _WINDOW_START_MS,
    price: float = 0.72,
    size: float = 10.0,
    side: str = "BUY",
) -> Tick:
    """Create a Tick instance for testing.

    Args:
        asset_id: Token identifier.
        condition_id: Market condition identifier.
        timestamp: Epoch milliseconds.
        price: Trade price.
        size: Trade size.
        side: Trade side.

    Returns:
        A new Tick instance.

    """
    return Tick(
        asset_id=asset_id,
        condition_id=condition_id,
        price=price,
        size=size,
        side=side,
        fee_rate_bps=_FEE_BPS,
        timestamp=timestamp,
        received_at=timestamp + 50,
    )


class TestMarketWindow:
    """Tests for the MarketWindow dataclass."""

    def test_create_market_window(self) -> None:
        """Create a MarketWindow with expected fields."""
        window = MarketWindow(
            condition_id=_CONDITION_ID,
            start_ms=_WINDOW_ALIGNED_MS,
            end_ms=_WINDOW_ALIGNED_MS + _FIVE_MINUTES_MS,
            end_date="2023-11-14T22:35:00+00:00",
        )

        assert window.condition_id == _CONDITION_ID
        assert window.end_ms - window.start_ms == _FIVE_MINUTES_MS


class TestSnapshotBuilderDetectWindow:
    """Tests for SnapshotBuilder.detect_window."""

    def test_detect_window_aligns_to_five_minute_boundary(self) -> None:
        """Detect window start aligned down to nearest 5-minute boundary."""
        ticks = [
            _make_tick(timestamp=_WINDOW_ALIGNED_MS + 30_000),
            _make_tick(timestamp=_WINDOW_ALIGNED_MS + 60_000),
        ]
        builder = SnapshotBuilder(bucket_seconds=_BUCKET_SECONDS)

        window = builder.detect_window(_CONDITION_ID, ticks)

        assert window.start_ms == _WINDOW_ALIGNED_MS
        assert window.end_ms == _WINDOW_ALIGNED_MS + _FIVE_MINUTES_MS

    def test_detect_window_raises_on_empty_ticks(self) -> None:
        """Raise ValueError when given an empty tick list."""
        builder = SnapshotBuilder(bucket_seconds=_BUCKET_SECONDS)

        with pytest.raises(ValueError, match="empty"):
            builder.detect_window(_CONDITION_ID, [])


class TestSnapshotBuilderBuildSnapshots:
    """Tests for SnapshotBuilder.build_snapshots."""

    def test_single_asset_produces_snapshots(self) -> None:
        """Build snapshots from ticks of a single asset (YES side)."""
        window = MarketWindow(
            condition_id=_CONDITION_ID,
            start_ms=_WINDOW_ALIGNED_MS,
            end_ms=_WINDOW_ALIGNED_MS + _FIVE_MINUTES_MS,
            end_date="2023-11-14T22:35:00+00:00",
        )
        ticks = [
            _make_tick(
                asset_id=_ASSET_YES,
                timestamp=_WINDOW_ALIGNED_MS + 1000,
                price=0.72,
            ),
            _make_tick(
                asset_id=_ASSET_YES,
                timestamp=_WINDOW_ALIGNED_MS + 2000,
                price=0.85,
            ),
        ]
        builder = SnapshotBuilder(bucket_seconds=_BUCKET_SECONDS)

        snapshots = builder.build_snapshots(ticks, window)

        assert len(snapshots) == _EXPECTED_BUCKETS_300
        assert snapshots[0].condition_id == _CONDITION_ID
        # First bucket (0s) has no tick — forward-filled from first available
        # Bucket at second 1 should have price 0.72
        assert snapshots[1].yes_price == Decimal("0.72")
        assert snapshots[1].no_price == Decimal("0.28")

    def test_two_assets_assigns_yes_no_lexicographically(self) -> None:
        """Lexicographically smaller asset_id is assigned as YES."""
        window = MarketWindow(
            condition_id=_CONDITION_ID,
            start_ms=_WINDOW_ALIGNED_MS,
            end_ms=_WINDOW_ALIGNED_MS + _FIVE_MINUTES_MS,
            end_date="2023-11-14T22:35:00+00:00",
        )
        # _ASSET_YES ("asset_aaa") < _ASSET_NO ("asset_zzz"), so YES = aaa
        ticks = [
            _make_tick(
                asset_id=_ASSET_YES,
                timestamp=_WINDOW_ALIGNED_MS + 1000,
                price=0.80,
            ),
            _make_tick(
                asset_id=_ASSET_NO,
                timestamp=_WINDOW_ALIGNED_MS + 1000,
                price=0.20,
            ),
        ]
        builder = SnapshotBuilder(bucket_seconds=_BUCKET_SECONDS)

        snapshots = builder.build_snapshots(ticks, window)

        # Snapshot at bucket 1 should use the YES asset price
        assert snapshots[1].yes_price == Decimal("0.80")
        assert snapshots[1].no_price == Decimal("0.20")

    def test_forward_fills_gaps(self) -> None:
        """Gaps between ticks are forward-filled with last known price."""
        window = MarketWindow(
            condition_id=_CONDITION_ID,
            start_ms=_WINDOW_ALIGNED_MS,
            end_ms=_WINDOW_ALIGNED_MS + _FIVE_MINUTES_MS,
            end_date="2023-11-14T22:35:00+00:00",
        )
        ticks = [
            _make_tick(
                asset_id=_ASSET_YES,
                timestamp=_WINDOW_ALIGNED_MS + 1000,
                price=0.72,
            ),
            _make_tick(
                asset_id=_ASSET_YES,
                timestamp=_WINDOW_ALIGNED_MS + 100_000,
                price=0.90,
            ),
        ]
        builder = SnapshotBuilder(bucket_seconds=_BUCKET_SECONDS)

        snapshots = builder.build_snapshots(ticks, window)

        # Bucket 50 (50s) should still be forward-filled from 0.72
        bucket_50_idx = 50
        assert snapshots[bucket_50_idx].yes_price == Decimal("0.72")
        # Bucket 100 (100s) should have 0.90
        bucket_100_idx = 100
        assert snapshots[bucket_100_idx].yes_price == Decimal("0.90")

    def test_last_price_wins_within_bucket(self) -> None:
        """Multiple ticks in one bucket: last price wins."""
        window = MarketWindow(
            condition_id=_CONDITION_ID,
            start_ms=_WINDOW_ALIGNED_MS,
            end_ms=_WINDOW_ALIGNED_MS + _FIVE_MINUTES_MS,
            end_date="2023-11-14T22:35:00+00:00",
        )
        # Both ticks fall in bucket index 1 (second 1)
        ticks = [
            _make_tick(
                asset_id=_ASSET_YES,
                timestamp=_WINDOW_ALIGNED_MS + 1100,
                price=0.70,
            ),
            _make_tick(
                asset_id=_ASSET_YES,
                timestamp=_WINDOW_ALIGNED_MS + 1900,
                price=0.85,
            ),
        ]
        builder = SnapshotBuilder(bucket_seconds=_BUCKET_SECONDS)

        snapshots = builder.build_snapshots(ticks, window)

        assert snapshots[1].yes_price == Decimal("0.85")

    def test_custom_bucket_size(self) -> None:
        """Custom bucket size produces fewer snapshots."""
        window = MarketWindow(
            condition_id=_CONDITION_ID,
            start_ms=_WINDOW_ALIGNED_MS,
            end_ms=_WINDOW_ALIGNED_MS + _FIVE_MINUTES_MS,
            end_date="2023-11-14T22:35:00+00:00",
        )
        ticks = [
            _make_tick(
                asset_id=_ASSET_YES,
                timestamp=_WINDOW_ALIGNED_MS + 1000,
                price=0.72,
            ),
        ]
        bucket_seconds_5 = 5
        builder = SnapshotBuilder(bucket_seconds=bucket_seconds_5)

        snapshots = builder.build_snapshots(ticks, window)

        expected_buckets = 60
        assert len(snapshots) == expected_buckets

    def test_snapshot_timestamps_in_epoch_seconds(self) -> None:
        """Snapshot timestamps are in epoch seconds, not milliseconds."""
        window = MarketWindow(
            condition_id=_CONDITION_ID,
            start_ms=_WINDOW_ALIGNED_MS,
            end_ms=_WINDOW_ALIGNED_MS + _FIVE_MINUTES_MS,
            end_date="2023-11-14T22:35:00+00:00",
        )
        ticks = [
            _make_tick(
                asset_id=_ASSET_YES,
                timestamp=_WINDOW_ALIGNED_MS + 1000,
                price=0.72,
            ),
        ]
        builder = SnapshotBuilder(bucket_seconds=_BUCKET_SECONDS)

        snapshots = builder.build_snapshots(ticks, window)

        # Timestamps should be epoch seconds (window_start_ms // 1000)
        expected_first_ts = _WINDOW_ALIGNED_MS // 1000
        assert snapshots[0].timestamp == expected_first_ts
        assert snapshots[1].timestamp == expected_first_ts + 1

    def test_empty_ticks_raises(self) -> None:
        """Raise ValueError when ticks list is empty."""
        window = MarketWindow(
            condition_id=_CONDITION_ID,
            start_ms=_WINDOW_ALIGNED_MS,
            end_ms=_WINDOW_ALIGNED_MS + _FIVE_MINUTES_MS,
            end_date="2023-11-14T22:35:00+00:00",
        )
        builder = SnapshotBuilder(bucket_seconds=_BUCKET_SECONDS)

        with pytest.raises(ValueError, match="empty"):
            builder.build_snapshots([], window)

    def test_end_date_set_on_all_snapshots(self) -> None:
        """All snapshots have the correct end_date from the window."""
        end_date = "2023-11-14T22:35:00+00:00"
        window = MarketWindow(
            condition_id=_CONDITION_ID,
            start_ms=_WINDOW_ALIGNED_MS,
            end_ms=_WINDOW_ALIGNED_MS + _FIVE_MINUTES_MS,
            end_date=end_date,
        )
        ticks = [
            _make_tick(
                asset_id=_ASSET_YES,
                timestamp=_WINDOW_ALIGNED_MS + 1000,
                price=0.72,
            ),
        ]
        builder = SnapshotBuilder(bucket_seconds=_BUCKET_SECONDS)

        snapshots = builder.build_snapshots(ticks, window)

        assert all(s.end_date == end_date for s in snapshots)
