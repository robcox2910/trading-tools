"""Tests for the snapshot builder that converts ticks to MarketSnapshots."""

from decimal import Decimal

import pytest

from trading_tools.apps.tick_collector.models import OrderBookSnapshot, Tick
from trading_tools.apps.tick_collector.snapshot_builder import (
    MarketWindow,
    SnapshotBuilder,
    _find_nearest_book,
)

_CONDITION_ID = "cond_abc123"
_ASSET_YES = "asset_aaa"
_ASSET_NO = "asset_zzz"
_FEE_BPS = 200
_FIVE_MINUTES_MS = 300_000
_FIFTEEN_MINUTES_MS = 900_000
_WINDOW_START_MS = 1_700_000_100_000
_WINDOW_ALIGNED_MS = 1_700_000_100_000 - (1_700_000_100_000 % _FIVE_MINUTES_MS)
_WINDOW_ALIGNED_15M_MS = 1_700_000_100_000 - (1_700_000_100_000 % _FIFTEEN_MINUTES_MS)
_BUCKET_SECONDS = 1
_EXPECTED_BUCKETS_300 = 300
_EXPECTED_BUCKETS_900 = 900
_WINDOW_MINUTES_15 = 15


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
        """Detect window end aligned up to next 5-minute boundary from latest tick."""
        ticks = [
            _make_tick(timestamp=_WINDOW_ALIGNED_MS + 30_000),
            _make_tick(timestamp=_WINDOW_ALIGNED_MS + 60_000),
        ]
        builder = SnapshotBuilder(bucket_seconds=_BUCKET_SECONDS)

        window = builder.detect_window(_CONDITION_ID, ticks)

        assert window.start_ms == _WINDOW_ALIGNED_MS
        assert window.end_ms == _WINDOW_ALIGNED_MS + _FIVE_MINUTES_MS

    def test_detect_window_with_pre_market_ticks(self) -> None:
        """Align to window containing the latest tick, not the earliest.

        When ticks arrive before the market window opens (pre-market
        trading), the window must cover the latest tick's boundary so that
        the resolution period is correctly captured.
        """
        # Simulate ticks arriving 5 minutes before a 15-minute window opens
        # and continuing through to near the window end
        pre_market_ms = _WINDOW_ALIGNED_15M_MS - 300_000  # 5 min before boundary
        late_tick_ms = _WINDOW_ALIGNED_15M_MS + 800_000  # ~13 min into window
        ticks = [
            _make_tick(timestamp=pre_market_ms),
            _make_tick(timestamp=_WINDOW_ALIGNED_15M_MS + 10_000),
            _make_tick(timestamp=late_tick_ms),
        ]
        builder = SnapshotBuilder(
            bucket_seconds=_BUCKET_SECONDS,
            window_minutes=_WINDOW_MINUTES_15,
        )

        window = builder.detect_window(_CONDITION_ID, ticks)

        # Window should cover the latest tick, not be based on earliest tick
        assert window.start_ms == _WINDOW_ALIGNED_15M_MS
        assert window.end_ms == _WINDOW_ALIGNED_15M_MS + _FIFTEEN_MINUTES_MS

    def test_detect_window_raises_on_empty_ticks(self) -> None:
        """Raise ValueError when given an empty tick list."""
        builder = SnapshotBuilder(bucket_seconds=_BUCKET_SECONDS)

        with pytest.raises(ValueError, match="empty"):
            builder.detect_window(_CONDITION_ID, [])


class TestDetectAllWindows:
    """Tests for SnapshotBuilder.detect_all_windows."""

    def test_ticks_spanning_two_windows_returns_two_pairs(self) -> None:
        """Ticks in two different 5-minute boundaries produce two window pairs."""
        # Window 1: ticks at aligned boundary + 0-4 min
        # Window 2: ticks at aligned boundary + 5-9 min (next window)
        base = _WINDOW_ALIGNED_MS
        ticks = [
            # Window 1 (5 ticks)
            _make_tick(timestamp=base + 10_000, price=0.50),
            _make_tick(timestamp=base + 20_000, price=0.52),
            _make_tick(timestamp=base + 30_000, price=0.55),
            _make_tick(timestamp=base + 40_000, price=0.58),
            _make_tick(timestamp=base + 50_000, price=0.60),
            # Window 2 (5 ticks)
            _make_tick(timestamp=base + _FIVE_MINUTES_MS + 10_000, price=0.70),
            _make_tick(timestamp=base + _FIVE_MINUTES_MS + 20_000, price=0.75),
            _make_tick(timestamp=base + _FIVE_MINUTES_MS + 30_000, price=0.80),
            _make_tick(timestamp=base + _FIVE_MINUTES_MS + 40_000, price=0.85),
            _make_tick(timestamp=base + _FIVE_MINUTES_MS + 50_000, price=0.90),
        ]
        builder = SnapshotBuilder(bucket_seconds=_BUCKET_SECONDS)

        result = builder.detect_all_windows(_CONDITION_ID, ticks)

        expected_windows = 2
        assert len(result) == expected_windows
        # First window starts at aligned boundary
        assert result[0][0].start_ms == base
        assert result[0][0].end_ms == base + _FIVE_MINUTES_MS
        expected_ticks_per_window = 5
        assert len(result[0][1]) == expected_ticks_per_window
        # Second window starts at next boundary
        assert result[1][0].start_ms == base + _FIVE_MINUTES_MS
        assert result[1][0].end_ms == base + 2 * _FIVE_MINUTES_MS
        assert len(result[1][1]) == expected_ticks_per_window

    def test_ticks_in_single_window_returns_one_pair(self) -> None:
        """Ticks within a single 5-minute boundary produce one window pair."""
        base = _WINDOW_ALIGNED_MS
        ticks = [
            _make_tick(timestamp=base + 10_000, price=0.50),
            _make_tick(timestamp=base + 20_000, price=0.55),
            _make_tick(timestamp=base + 30_000, price=0.60),
            _make_tick(timestamp=base + 40_000, price=0.65),
            _make_tick(timestamp=base + 50_000, price=0.70),
        ]
        builder = SnapshotBuilder(bucket_seconds=_BUCKET_SECONDS)

        result = builder.detect_all_windows(_CONDITION_ID, ticks)

        expected_windows = 1
        assert len(result) == expected_windows
        assert result[0][0].start_ms == base
        expected_ticks = 5
        assert len(result[0][1]) == expected_ticks

    def test_sparse_stragglers_filtered_out(self) -> None:
        """Windows with fewer than MIN_TICKS_PER_WINDOW are excluded."""
        base = _WINDOW_ALIGNED_MS
        ticks = [
            # Window 1: 5 ticks (above threshold)
            _make_tick(timestamp=base + 10_000, price=0.50),
            _make_tick(timestamp=base + 20_000, price=0.55),
            _make_tick(timestamp=base + 30_000, price=0.60),
            _make_tick(timestamp=base + 40_000, price=0.65),
            _make_tick(timestamp=base + 50_000, price=0.70),
            # Window 2: 2 ticks (below threshold — stragglers)
            _make_tick(timestamp=base + _FIVE_MINUTES_MS + 10_000, price=0.99),
            _make_tick(timestamp=base + _FIVE_MINUTES_MS + 20_000, price=0.99),
        ]
        builder = SnapshotBuilder(bucket_seconds=_BUCKET_SECONDS)

        result = builder.detect_all_windows(_CONDITION_ID, ticks)

        # Only the first window survives filtering
        expected_windows = 1
        assert len(result) == expected_windows
        assert result[0][0].start_ms == base

    def test_empty_ticks_raises(self) -> None:
        """Raise ValueError when given an empty tick list."""
        builder = SnapshotBuilder(bucket_seconds=_BUCKET_SECONDS)

        with pytest.raises(ValueError, match="empty"):
            builder.detect_all_windows(_CONDITION_ID, [])

    def test_windows_have_correct_end_date(self) -> None:
        """Each returned window has a valid ISO-format end_date."""
        base = _WINDOW_ALIGNED_MS
        ticks = [
            _make_tick(timestamp=base + 10_000, price=0.50),
            _make_tick(timestamp=base + 20_000, price=0.55),
            _make_tick(timestamp=base + 30_000, price=0.60),
            _make_tick(timestamp=base + 40_000, price=0.65),
            _make_tick(timestamp=base + 50_000, price=0.70),
        ]
        builder = SnapshotBuilder(bucket_seconds=_BUCKET_SECONDS)

        result = builder.detect_all_windows(_CONDITION_ID, ticks)

        expected_windows = 1
        assert len(result) == expected_windows
        window = result[0][0]
        assert window.condition_id == _CONDITION_ID
        assert window.end_date.endswith("+00:00")

    def test_all_below_threshold_returns_empty(self) -> None:
        """Return empty list when all windows have fewer ticks than threshold."""
        base = _WINDOW_ALIGNED_MS
        ticks = [
            _make_tick(timestamp=base + 10_000, price=0.50),
            _make_tick(timestamp=base + 20_000, price=0.55),
        ]
        builder = SnapshotBuilder(bucket_seconds=_BUCKET_SECONDS)

        result = builder.detect_all_windows(_CONDITION_ID, ticks)

        assert len(result) == 0


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


class TestSnapshotBuilder15MinuteWindow:
    """Tests for SnapshotBuilder with 15-minute market windows."""

    def test_detect_window_aligns_to_15_minute_boundary(self) -> None:
        """Detect window end aligned up to next 15-minute boundary from latest tick."""
        ticks = [
            _make_tick(timestamp=_WINDOW_ALIGNED_15M_MS + 30_000),
        ]
        builder = SnapshotBuilder(
            bucket_seconds=_BUCKET_SECONDS,
            window_minutes=_WINDOW_MINUTES_15,
        )

        window = builder.detect_window(_CONDITION_ID, ticks)

        assert window.start_ms == _WINDOW_ALIGNED_15M_MS
        assert window.end_ms == _WINDOW_ALIGNED_15M_MS + _FIFTEEN_MINUTES_MS

    def test_15_minute_window_produces_900_buckets(self) -> None:
        """Build 900 one-second buckets for a 15-minute window."""
        window = MarketWindow(
            condition_id=_CONDITION_ID,
            start_ms=_WINDOW_ALIGNED_15M_MS,
            end_ms=_WINDOW_ALIGNED_15M_MS + _FIFTEEN_MINUTES_MS,
            end_date="2023-11-14T22:35:00+00:00",
        )
        ticks = [
            _make_tick(
                asset_id=_ASSET_YES,
                timestamp=_WINDOW_ALIGNED_15M_MS + 1000,
                price=0.72,
            ),
            _make_tick(
                asset_id=_ASSET_YES,
                timestamp=_WINDOW_ALIGNED_15M_MS + 600_000,
                price=0.85,
            ),
        ]
        builder = SnapshotBuilder(
            bucket_seconds=_BUCKET_SECONDS,
            window_minutes=_WINDOW_MINUTES_15,
        )

        snapshots = builder.build_snapshots(ticks, window)

        assert len(snapshots) == _EXPECTED_BUCKETS_900
        # Tick at 600s should be in bucket 600
        bucket_600_idx = 600
        assert snapshots[bucket_600_idx].yes_price == Decimal("0.85")
        # Bucket 300 (5 min mark) should be forward-filled from 0.72
        bucket_300_idx = 300
        assert snapshots[bucket_300_idx].yes_price == Decimal("0.72")


def _make_book_snapshot(
    token_id: str = _ASSET_YES,
    timestamp: int = _WINDOW_ALIGNED_MS,
    bids_json: str = '[["0.72", "100"]]',
    asks_json: str = '[["0.74", "150"]]',
    spread: float = 0.02,
    midpoint: float = 0.73,
) -> OrderBookSnapshot:
    """Create an OrderBookSnapshot instance for testing.

    Args:
        token_id: CLOB token identifier.
        timestamp: Epoch milliseconds.
        bids_json: JSON bid levels.
        asks_json: JSON ask levels.
        spread: Best ask minus best bid.
        midpoint: Average of best bid and best ask.

    Returns:
        A new OrderBookSnapshot instance.

    """
    return OrderBookSnapshot(
        token_id=token_id,
        timestamp=timestamp,
        bids_json=bids_json,
        asks_json=asks_json,
        spread=spread,
        midpoint=midpoint,
    )


class TestBuildSnapshotsWithBookData:
    """Tests for build_snapshots with order book enrichment."""

    def test_build_snapshots_with_book_data(self) -> None:
        """Pass pre-loaded books and verify non-empty order_book in snapshots."""
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
        book_snapshots = {
            _ASSET_YES: [
                _make_book_snapshot(
                    token_id=_ASSET_YES,
                    timestamp=_WINDOW_ALIGNED_MS + 500,
                ),
            ],
        }
        builder = SnapshotBuilder(bucket_seconds=_BUCKET_SECONDS)

        snapshots = builder.build_snapshots(ticks, window, book_snapshots=book_snapshots)

        # Snapshots should have non-empty order book from the matched book data
        assert len(snapshots) == _EXPECTED_BUCKETS_300
        # At least one snapshot should have bids populated
        matched = snapshots[0]
        assert len(matched.order_book.bids) > 0
        assert matched.order_book.bids[0].price == Decimal("0.72")
        assert matched.order_book.asks[0].price == Decimal("0.74")

    def test_build_snapshots_without_book_data(self) -> None:
        """No books passed produces empty order books (backward compat)."""
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

        # All snapshots should have empty order books
        assert all(len(s.order_book.bids) == 0 for s in snapshots)
        assert all(len(s.order_book.asks) == 0 for s in snapshots)

    def test_build_snapshots_with_no_matching_token(self) -> None:
        """Books for a different token produce empty order books."""
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
        book_snapshots = {
            "other_token": [
                _make_book_snapshot(
                    token_id="other_token",
                    timestamp=_WINDOW_ALIGNED_MS + 500,
                ),
            ],
        }
        builder = SnapshotBuilder(bucket_seconds=_BUCKET_SECONDS)

        snapshots = builder.build_snapshots(ticks, window, book_snapshots=book_snapshots)

        assert all(len(s.order_book.bids) == 0 for s in snapshots)


class TestFindNearestBook:
    """Tests for the _find_nearest_book helper."""

    def test_find_nearest_book_returns_closest(self) -> None:
        """Return the book snapshot closest to the target timestamp."""
        books = [
            _make_book_snapshot(timestamp=_WINDOW_ALIGNED_MS),
            _make_book_snapshot(
                timestamp=_WINDOW_ALIGNED_MS + 5000,
                bids_json='[["0.80", "200"]]',
                asks_json='[["0.82", "300"]]',
                spread=0.02,
                midpoint=0.81,
            ),
            _make_book_snapshot(timestamp=_WINDOW_ALIGNED_MS + 10000),
        ]

        result = _find_nearest_book(books, _WINDOW_ALIGNED_MS + 4800)

        assert result is not None
        assert result.bids[0].price == Decimal("0.80")
        assert result.bids[0].size == Decimal(200)

    def test_find_nearest_book_exact_match(self) -> None:
        """Return exact match when target equals a snapshot timestamp."""
        books = [
            _make_book_snapshot(timestamp=_WINDOW_ALIGNED_MS),
            _make_book_snapshot(timestamp=_WINDOW_ALIGNED_MS + 5000),
        ]

        result = _find_nearest_book(books, _WINDOW_ALIGNED_MS)

        assert result is not None
        assert result.token_id == _ASSET_YES

    def test_find_nearest_book_empty_list(self) -> None:
        """Return None when the book list is empty."""
        result = _find_nearest_book([], _WINDOW_ALIGNED_MS)

        assert result is None

    def test_find_nearest_book_before_all(self) -> None:
        """Return the first book when target is before all snapshots."""
        books = [
            _make_book_snapshot(timestamp=_WINDOW_ALIGNED_MS + 5000),
            _make_book_snapshot(timestamp=_WINDOW_ALIGNED_MS + 10000),
        ]

        result = _find_nearest_book(books, _WINDOW_ALIGNED_MS)

        assert result is not None
        assert result.token_id == _ASSET_YES

    def test_find_nearest_book_after_all(self) -> None:
        """Return the last book when target is after all snapshots."""
        books = [
            _make_book_snapshot(timestamp=_WINDOW_ALIGNED_MS),
            _make_book_snapshot(timestamp=_WINDOW_ALIGNED_MS + 5000),
        ]

        result = _find_nearest_book(books, _WINDOW_ALIGNED_MS + 100000)

        assert result is not None
