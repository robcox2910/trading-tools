"""Tests for the whale repository."""

import pytest
import pytest_asyncio

from trading_tools.apps.whale_monitor.models import WhaleTrade
from trading_tools.apps.whale_monitor.repository import WhaleRepository

_ADDRESS_A = "0xaaaa1111"
_ADDRESS_B = "0xbbbb2222"
_LABEL_A = "Whale-A"
_LABEL_B = "Whale-B"
_CONDITION_A = "cond_aaa"
_BASE_TS = 1700000000
_COLLECTED_AT = 1700000000000
_BATCH_COUNT_5 = 5
_RANGE_COUNT_3 = 3
_COMBINED_COUNT_7 = 7


def _make_trade(
    whale_address: str = _ADDRESS_A,
    transaction_hash: str = "tx_001",
    timestamp: int = _BASE_TS,
    side: str = "BUY",
    size: float = 50.0,
    price: float = 0.72,
    condition_id: str = _CONDITION_A,
) -> WhaleTrade:
    """Create a WhaleTrade instance for testing.

    Args:
        whale_address: Whale proxy wallet address.
        transaction_hash: Unique transaction hash.
        timestamp: Epoch seconds.
        side: Trade side.
        size: Token quantity.
        price: Execution price.
        condition_id: Market condition ID.

    Returns:
        A new WhaleTrade instance.

    """
    return WhaleTrade(
        whale_address=whale_address,
        transaction_hash=transaction_hash,
        side=side,
        asset_id="asset_test",
        condition_id=condition_id,
        size=size,
        price=price,
        timestamp=timestamp,
        title="Test Market",
        slug="test-market",
        outcome="Up",
        outcome_index=0,
        collected_at=_COLLECTED_AT,
    )


@pytest_asyncio.fixture
async def repo() -> WhaleRepository:
    """Create an in-memory SQLite repository for testing.

    Returns:
        Initialised WhaleRepository with an in-memory database.

    """
    repository = WhaleRepository("sqlite+aiosqlite:///:memory:")
    await repository.init_db()
    return repository


class TestWhaleRepository:
    """Tests for WhaleRepository CRUD operations."""

    @pytest.mark.asyncio
    async def test_add_whale(self, repo: WhaleRepository) -> None:
        """Add a whale and verify it is stored."""
        whale = await repo.add_whale(_ADDRESS_A, _LABEL_A)

        assert whale.address == _ADDRESS_A.lower()
        assert whale.label == _LABEL_A
        assert whale.active is True

    @pytest.mark.asyncio
    async def test_add_whale_idempotent(self, repo: WhaleRepository) -> None:
        """Adding the same address twice returns the existing record."""
        whale1 = await repo.add_whale(_ADDRESS_A, _LABEL_A)
        whale2 = await repo.add_whale(_ADDRESS_A, "Different Label")

        assert whale1.address == whale2.address
        assert whale2.label == _LABEL_A

    @pytest.mark.asyncio
    async def test_add_whale_lowercases_address(self, repo: WhaleRepository) -> None:
        """Whale addresses are stored in lowercase."""
        whale = await repo.add_whale("0xABCD1234", _LABEL_A)
        assert whale.address == "0xabcd1234"

    @pytest.mark.asyncio
    async def test_get_active_whales(self, repo: WhaleRepository) -> None:
        """Return only active whales."""
        await repo.add_whale(_ADDRESS_A, _LABEL_A)
        await repo.add_whale(_ADDRESS_B, _LABEL_B)

        whales = await repo.get_active_whales()
        assert len(whales) == 2  # noqa: PLR2004

    @pytest.mark.asyncio
    async def test_save_and_count(self, repo: WhaleRepository) -> None:
        """Save trades and verify the count."""
        trades = [
            _make_trade(transaction_hash=f"tx_{i}", timestamp=_BASE_TS + i)
            for i in range(_BATCH_COUNT_5)
        ]
        await repo.save_trades(trades)

        count = await repo.get_trade_count()
        assert count == _BATCH_COUNT_5

    @pytest.mark.asyncio
    async def test_save_empty_list(self, repo: WhaleRepository) -> None:
        """Save an empty list without error."""
        await repo.save_trades([])
        count = await repo.get_trade_count()
        assert count == 0

    @pytest.mark.asyncio
    async def test_get_trades_time_range(self, repo: WhaleRepository) -> None:
        """Query trades within a time range returns correct subset."""
        trades = [
            _make_trade(transaction_hash=f"tx_{i}", timestamp=_BASE_TS + i * 100) for i in range(10)
        ]
        await repo.save_trades(trades)

        result = await repo.get_trades(
            _ADDRESS_A,
            start_ts=_BASE_TS + 200,
            end_ts=_BASE_TS + 400,
        )

        assert len(result) == _RANGE_COUNT_3
        assert result[0].timestamp == _BASE_TS + 200
        assert result[-1].timestamp == _BASE_TS + 400

    @pytest.mark.asyncio
    async def test_get_trades_filters_by_address(self, repo: WhaleRepository) -> None:
        """Query returns only trades for the requested address."""
        trades = [
            _make_trade(whale_address=_ADDRESS_A, transaction_hash="tx_a"),
            _make_trade(whale_address=_ADDRESS_B, transaction_hash="tx_b"),
            _make_trade(whale_address=_ADDRESS_A, transaction_hash="tx_c"),
        ]
        await repo.save_trades(trades)

        result = await repo.get_trades(_ADDRESS_A, start_ts=0, end_ts=_BASE_TS + 1000)
        assert len(result) == 2  # noqa: PLR2004
        assert all(t.whale_address == _ADDRESS_A for t in result)

    @pytest.mark.asyncio
    async def test_get_trades_ordered_by_timestamp(self, repo: WhaleRepository) -> None:
        """Results are returned in ascending timestamp order."""
        trades = [
            _make_trade(transaction_hash="tx_c", timestamp=_BASE_TS + 300),
            _make_trade(transaction_hash="tx_a", timestamp=_BASE_TS + 100),
            _make_trade(transaction_hash="tx_b", timestamp=_BASE_TS + 200),
        ]
        await repo.save_trades(trades)

        result = await repo.get_trades(_ADDRESS_A, start_ts=0, end_ts=_BASE_TS + 1000)
        timestamps = [t.timestamp for t in result]
        assert timestamps == sorted(timestamps)

    @pytest.mark.asyncio
    async def test_get_existing_hashes(self, repo: WhaleRepository) -> None:
        """Return the subset of transaction hashes already stored."""
        trades = [_make_trade(transaction_hash="tx_existing")]
        await repo.save_trades(trades)

        existing = await repo.get_existing_hashes({"tx_existing", "tx_new"})
        assert existing == {"tx_existing"}

    @pytest.mark.asyncio
    async def test_get_existing_hashes_empty(self, repo: WhaleRepository) -> None:
        """Return empty set when given empty input."""
        result = await repo.get_existing_hashes(set())
        assert result == set()

    @pytest.mark.asyncio
    async def test_get_trade_count_by_address(self, repo: WhaleRepository) -> None:
        """Count trades filtered by address."""
        trades = [
            _make_trade(whale_address=_ADDRESS_A, transaction_hash="tx_a1"),
            _make_trade(whale_address=_ADDRESS_A, transaction_hash="tx_a2"),
            _make_trade(whale_address=_ADDRESS_B, transaction_hash="tx_b1"),
        ]
        await repo.save_trades(trades)

        count_a = await repo.get_trade_count(_ADDRESS_A)
        count_b = await repo.get_trade_count(_ADDRESS_B)
        assert count_a == 2  # noqa: PLR2004
        assert count_b == 1

    @pytest.mark.asyncio
    async def test_batch_insert_multiple_batches(self, repo: WhaleRepository) -> None:
        """Multiple batch inserts accumulate correctly."""
        batch1 = [_make_trade(transaction_hash=f"tx_1_{i}") for i in range(3)]
        batch2 = [_make_trade(transaction_hash=f"tx_2_{i}") for i in range(4)]
        await repo.save_trades(batch1)
        await repo.save_trades(batch2)

        count = await repo.get_trade_count()
        assert count == _COMBINED_COUNT_7

    @pytest.mark.asyncio
    async def test_init_db_idempotent(self, repo: WhaleRepository) -> None:
        """Calling init_db multiple times does not raise."""
        await repo.init_db()
        await repo.init_db()
        count = await repo.get_trade_count()
        assert count == 0

    @pytest.mark.asyncio
    async def test_close(self, repo: WhaleRepository) -> None:
        """Close disposes the engine without raising."""
        await repo.close()
