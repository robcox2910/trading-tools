"""Tests for the tick repository."""

import pytest
import pytest_asyncio

from trading_tools.apps.tick_collector.models import OrderBookSnapshot, Tick
from trading_tools.apps.tick_collector.repository import TickRepository

_ASSET_A = "asset_aaa"
_ASSET_B = "asset_bbb"
_CONDITION_A = "cond_aaa"
_CONDITION_B = "cond_bbb"
_TOKEN_A = "token_aaa"
_TOKEN_B = "token_bbb"
_BASE_TS = 1700000000000
_FEE_BPS = 200
_BATCH_COUNT_5 = 5
_RANGE_COUNT_4 = 4
_ASSET_FILTER_COUNT_2 = 2
_COMBINED_COUNT_7 = 7
_CONDITION_TICKS_COUNT_3 = 3
_DISTINCT_CONDITIONS_COUNT_2 = 2
_BOOK_BATCH_COUNT_3 = 3
_BOOK_RANGE_COUNT_2 = 2
_TOLERANCE_MS = 5000


def _make_tick(
    asset_id: str = _ASSET_A,
    condition_id: str = _CONDITION_A,
    timestamp: int = _BASE_TS,
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


@pytest_asyncio.fixture
async def repo() -> TickRepository:
    """Create an in-memory SQLite repository for testing.

    Returns:
        Initialised TickRepository with an in-memory database.

    """
    repository = TickRepository("sqlite+aiosqlite:///:memory:")
    await repository.init_db()
    return repository


class TestTickRepository:
    """Tests for TickRepository CRUD operations."""

    @pytest.mark.asyncio
    async def test_save_and_count(self, repo: TickRepository) -> None:
        """Save ticks and verify the count."""
        ticks = [_make_tick(timestamp=_BASE_TS + i) for i in range(_BATCH_COUNT_5)]
        await repo.save_ticks(ticks)

        count = await repo.get_tick_count()
        assert count == _BATCH_COUNT_5

    @pytest.mark.asyncio
    async def test_save_empty_list(self, repo: TickRepository) -> None:
        """Save an empty list without error."""
        await repo.save_ticks([])
        count = await repo.get_tick_count()
        assert count == 0

    @pytest.mark.asyncio
    async def test_get_ticks_time_range(self, repo: TickRepository) -> None:
        """Query ticks within a time range returns correct subset."""
        ticks = [_make_tick(timestamp=_BASE_TS + i * 1000) for i in range(10)]
        await repo.save_ticks(ticks)

        result = await repo.get_ticks(
            _ASSET_A,
            start_ms=_BASE_TS + 2000,
            end_ms=_BASE_TS + 5000,
        )

        assert len(result) == _RANGE_COUNT_4
        assert result[0].timestamp == _BASE_TS + 2000
        assert result[-1].timestamp == _BASE_TS + 5000

    @pytest.mark.asyncio
    async def test_get_ticks_filters_by_asset(self, repo: TickRepository) -> None:
        """Query returns only ticks for the requested asset."""
        ticks = [
            _make_tick(asset_id=_ASSET_A, timestamp=_BASE_TS),
            _make_tick(asset_id=_ASSET_B, timestamp=_BASE_TS + 1000),
            _make_tick(asset_id=_ASSET_A, timestamp=_BASE_TS + 2000),
        ]
        await repo.save_ticks(ticks)

        result = await repo.get_ticks(
            _ASSET_A,
            start_ms=_BASE_TS,
            end_ms=_BASE_TS + 5000,
        )

        assert len(result) == _ASSET_FILTER_COUNT_2
        assert all(t.asset_id == _ASSET_A for t in result)

    @pytest.mark.asyncio
    async def test_get_ticks_ordered_by_timestamp(self, repo: TickRepository) -> None:
        """Results are returned in ascending timestamp order."""
        ticks = [
            _make_tick(timestamp=_BASE_TS + 3000),
            _make_tick(timestamp=_BASE_TS + 1000),
            _make_tick(timestamp=_BASE_TS + 2000),
        ]
        await repo.save_ticks(ticks)

        result = await repo.get_ticks(
            _ASSET_A,
            start_ms=_BASE_TS,
            end_ms=_BASE_TS + 5000,
        )

        timestamps = [t.timestamp for t in result]
        assert timestamps == sorted(timestamps)

    @pytest.mark.asyncio
    async def test_get_ticks_empty_range(self, repo: TickRepository) -> None:
        """Query for a range with no ticks returns empty list."""
        ticks = [_make_tick(timestamp=_BASE_TS)]
        await repo.save_ticks(ticks)

        result = await repo.get_ticks(
            _ASSET_A,
            start_ms=_BASE_TS + 10000,
            end_ms=_BASE_TS + 20000,
        )

        assert result == []

    @pytest.mark.asyncio
    async def test_init_db_idempotent(self, repo: TickRepository) -> None:
        """Calling init_db multiple times does not raise."""
        await repo.init_db()
        await repo.init_db()
        count = await repo.get_tick_count()
        assert count == 0

    @pytest.mark.asyncio
    async def test_batch_insert_multiple_batches(self, repo: TickRepository) -> None:
        """Multiple batch inserts accumulate correctly."""
        batch1 = [_make_tick(timestamp=_BASE_TS + i) for i in range(3)]
        batch2 = [_make_tick(timestamp=_BASE_TS + 100 + i) for i in range(4)]
        await repo.save_ticks(batch1)
        await repo.save_ticks(batch2)

        count = await repo.get_tick_count()
        assert count == _COMBINED_COUNT_7

    @pytest.mark.asyncio
    async def test_get_distinct_condition_ids(self, repo: TickRepository) -> None:
        """Return unique condition IDs within a time range."""
        ticks = [
            _make_tick(condition_id=_CONDITION_A, timestamp=_BASE_TS),
            _make_tick(condition_id=_CONDITION_A, timestamp=_BASE_TS + 1000),
            _make_tick(condition_id=_CONDITION_B, timestamp=_BASE_TS + 2000),
            _make_tick(condition_id=_CONDITION_A, timestamp=_BASE_TS + 20000),
        ]
        await repo.save_ticks(ticks)

        result = await repo.get_distinct_condition_ids(
            start_ms=_BASE_TS,
            end_ms=_BASE_TS + 5000,
        )

        assert len(result) == _DISTINCT_CONDITIONS_COUNT_2
        assert set(result) == {_CONDITION_A, _CONDITION_B}

    @pytest.mark.asyncio
    async def test_get_distinct_condition_ids_empty_range(self, repo: TickRepository) -> None:
        """Return empty list when no ticks exist in range."""
        ticks = [_make_tick(condition_id=_CONDITION_A, timestamp=_BASE_TS)]
        await repo.save_ticks(ticks)

        result = await repo.get_distinct_condition_ids(
            start_ms=_BASE_TS + 10000,
            end_ms=_BASE_TS + 20000,
        )

        assert result == []

    @pytest.mark.asyncio
    async def test_get_ticks_by_condition(self, repo: TickRepository) -> None:
        """Return ticks filtered by condition_id and time range."""
        ticks = [
            _make_tick(
                asset_id=_ASSET_A,
                condition_id=_CONDITION_A,
                timestamp=_BASE_TS,
            ),
            _make_tick(
                asset_id=_ASSET_B,
                condition_id=_CONDITION_A,
                timestamp=_BASE_TS + 1000,
            ),
            _make_tick(
                asset_id=_ASSET_A,
                condition_id=_CONDITION_B,
                timestamp=_BASE_TS + 2000,
            ),
            _make_tick(
                asset_id=_ASSET_A,
                condition_id=_CONDITION_A,
                timestamp=_BASE_TS + 3000,
            ),
        ]
        await repo.save_ticks(ticks)

        result = await repo.get_ticks_by_condition(
            condition_id=_CONDITION_A,
            start_ms=_BASE_TS,
            end_ms=_BASE_TS + 5000,
        )

        assert len(result) == _CONDITION_TICKS_COUNT_3
        assert all(t.condition_id == _CONDITION_A for t in result)

    @pytest.mark.asyncio
    async def test_get_ticks_by_condition_ordered_by_timestamp(
        self,
        repo: TickRepository,
    ) -> None:
        """Return ticks ordered by ascending timestamp."""
        ticks = [
            _make_tick(condition_id=_CONDITION_A, timestamp=_BASE_TS + 3000),
            _make_tick(condition_id=_CONDITION_A, timestamp=_BASE_TS + 1000),
            _make_tick(condition_id=_CONDITION_A, timestamp=_BASE_TS + 2000),
        ]
        await repo.save_ticks(ticks)

        result = await repo.get_ticks_by_condition(
            condition_id=_CONDITION_A,
            start_ms=_BASE_TS,
            end_ms=_BASE_TS + 5000,
        )

        timestamps = [t.timestamp for t in result]
        assert timestamps == sorted(timestamps)

    @pytest.mark.asyncio
    async def test_get_ticks_by_condition_empty_range(self, repo: TickRepository) -> None:
        """Return empty list when no ticks match condition in range."""
        ticks = [_make_tick(condition_id=_CONDITION_A, timestamp=_BASE_TS)]
        await repo.save_ticks(ticks)

        result = await repo.get_ticks_by_condition(
            condition_id=_CONDITION_B,
            start_ms=_BASE_TS,
            end_ms=_BASE_TS + 5000,
        )

        assert result == []

    @pytest.mark.asyncio
    async def test_close(self, repo: TickRepository) -> None:
        """Close disposes the engine without raising."""
        await repo.close()


def _make_book_snapshot(
    token_id: str = _TOKEN_A,
    timestamp: int = _BASE_TS,
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


class TestOrderBookSnapshotRepository:
    """Tests for order book snapshot repository operations."""

    @pytest.mark.asyncio
    async def test_save_and_query_order_book_snapshots(
        self,
        repo: TickRepository,
    ) -> None:
        """Batch-insert order book snapshots and query by time range."""
        snapshots = [
            _make_book_snapshot(timestamp=_BASE_TS + i * 1000) for i in range(_BOOK_BATCH_COUNT_3)
        ]
        await repo.save_order_book_snapshots(snapshots)

        result = await repo.get_order_book_snapshots_in_range(
            _TOKEN_A,
            start_ms=_BASE_TS,
            end_ms=_BASE_TS + 1000,
        )

        assert len(result) == _BOOK_RANGE_COUNT_2
        assert result[0].timestamp == _BASE_TS
        assert result[1].timestamp == _BASE_TS + 1000

    @pytest.mark.asyncio
    async def test_save_empty_book_snapshots(self, repo: TickRepository) -> None:
        """Save an empty snapshot list without error."""
        await repo.save_order_book_snapshots([])

    @pytest.mark.asyncio
    async def test_get_order_book_snapshots_filters_by_token(
        self,
        repo: TickRepository,
    ) -> None:
        """Query returns only snapshots for the requested token."""
        snapshots = [
            _make_book_snapshot(token_id=_TOKEN_A, timestamp=_BASE_TS),
            _make_book_snapshot(token_id=_TOKEN_B, timestamp=_BASE_TS + 1000),
        ]
        await repo.save_order_book_snapshots(snapshots)

        result = await repo.get_order_book_snapshots_in_range(
            _TOKEN_A,
            start_ms=_BASE_TS,
            end_ms=_BASE_TS + 5000,
        )

        assert len(result) == 1
        assert result[0].token_id == _TOKEN_A

    @pytest.mark.asyncio
    async def test_get_nearest_book_snapshot(self, repo: TickRepository) -> None:
        """Return the closest snapshot within the tolerance window."""
        snapshots = [
            _make_book_snapshot(timestamp=_BASE_TS),
            _make_book_snapshot(timestamp=_BASE_TS + 3000),
            _make_book_snapshot(timestamp=_BASE_TS + 8000),
        ]
        await repo.save_order_book_snapshots(snapshots)

        result = await repo.get_nearest_book_snapshot(
            _TOKEN_A,
            timestamp_ms=_BASE_TS + 2500,
            tolerance_ms=_TOLERANCE_MS,
        )

        assert result is not None
        assert result.timestamp == _BASE_TS + 3000

    @pytest.mark.asyncio
    async def test_get_nearest_book_snapshot_none_outside_tolerance(
        self,
        repo: TickRepository,
    ) -> None:
        """Return None when no snapshot exists within the tolerance window."""
        snapshots = [_make_book_snapshot(timestamp=_BASE_TS)]
        await repo.save_order_book_snapshots(snapshots)

        result = await repo.get_nearest_book_snapshot(
            _TOKEN_A,
            timestamp_ms=_BASE_TS + 20000,
            tolerance_ms=_TOLERANCE_MS,
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_get_order_book_snapshots_ordered_by_timestamp(
        self,
        repo: TickRepository,
    ) -> None:
        """Verify snapshots are returned in ascending timestamp order."""
        snapshots = [
            _make_book_snapshot(timestamp=_BASE_TS + 3000),
            _make_book_snapshot(timestamp=_BASE_TS + 1000),
            _make_book_snapshot(timestamp=_BASE_TS + 2000),
        ]
        await repo.save_order_book_snapshots(snapshots)

        result = await repo.get_order_book_snapshots_in_range(
            _TOKEN_A,
            start_ms=_BASE_TS,
            end_ms=_BASE_TS + 5000,
        )

        timestamps = [s.timestamp for s in result]
        assert timestamps == sorted(timestamps)
