"""Tests for the tick repository."""

import pytest
import pytest_asyncio

from trading_tools.apps.tick_collector.models import Tick
from trading_tools.apps.tick_collector.repository import TickRepository

_ASSET_A = "asset_aaa"
_ASSET_B = "asset_bbb"
_CONDITION_A = "cond_aaa"
_CONDITION_B = "cond_bbb"
_BASE_TS = 1700000000000
_FEE_BPS = 200
_BATCH_COUNT_5 = 5
_RANGE_COUNT_4 = 4
_ASSET_FILTER_COUNT_2 = 2
_COMBINED_COUNT_7 = 7


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
    async def test_close(self, repo: TickRepository) -> None:
        """Close disposes the engine without raising."""
        await repo.close()
