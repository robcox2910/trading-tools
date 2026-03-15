"""Tests for the copy trade result repository."""

from decimal import Decimal

import pytest
import pytest_asyncio

from trading_tools.apps.whale_copy_trader.models import (
    CopyResult,
    CopyResultRecord,
    CopySignal,
    PositionState,
)
from trading_tools.apps.whale_copy_trader.repository import CopyResultRepository

_CONDITION_A = "cond_aaa"
_CONDITION_B = "cond_bbb"
_BASE_TS = 1_700_000_000
_DETECTED_AT = _BASE_TS - 300
_WINDOW_START = _BASE_TS - 300
_WINDOW_END = _BASE_TS
_DEFAULT_BIAS = Decimal("2.5")
_EXPECTED_RESULT_COUNT_2 = 2
_EXPECTED_RESULT_COUNT_3 = 3


def _make_signal(
    condition_id: str = _CONDITION_A,
    asset: str = "BTC-USD",
) -> CopySignal:
    """Create a CopySignal for testing.

    Args:
        condition_id: Market condition ID.
        asset: Spot trading pair.

    Returns:
        A CopySignal instance.

    """
    return CopySignal(
        condition_id=condition_id,
        title="Bitcoin Up or Down - Test",
        asset=asset,
        favoured_side="Up",
        bias_ratio=_DEFAULT_BIAS,
        trade_count=5,
        window_start_ts=_WINDOW_START,
        window_end_ts=_WINDOW_END,
        detected_at=_DETECTED_AT,
    )


def _make_copy_result(
    condition_id: str = _CONDITION_A,
    asset: str = "BTC-USD",
    exit_time: int = _BASE_TS,
    *,
    is_paper: bool = True,
    state: PositionState = PositionState.HEDGED,
    pnl: Decimal = Decimal("1.50"),
) -> CopyResult:
    """Create a CopyResult for testing.

    Args:
        condition_id: Market condition ID.
        asset: Spot trading pair.
        exit_time: Epoch seconds when position closed.
        is_paper: Whether this is a paper trade.
        state: Position state at close.
        pnl: Realised P&L.

    Returns:
        A CopyResult instance.

    """
    return CopyResult(
        signal=_make_signal(condition_id=condition_id, asset=asset),
        state=state,
        leg1_side="Up",
        leg1_entry=Decimal("0.55"),
        leg1_qty=Decimal("18.18"),
        hedge_entry=Decimal("0.35"),
        hedge_qty=Decimal("18.18"),
        total_cost_basis=Decimal("16.36"),
        entry_time=_BASE_TS - 100,
        exit_time=exit_time,
        winning_side="Up",
        pnl=pnl,
        is_paper=is_paper,
        order_ids=("order_1", "order_2"),
    )


class TestCopyResultRecord:
    """Tests for the CopyResultRecord ORM model."""

    def test_from_copy_result_maps_all_fields(self) -> None:
        """Verify all fields are correctly mapped from CopyResult."""
        result = _make_copy_result()
        record = CopyResultRecord.from_copy_result(result)

        assert record.condition_id == _CONDITION_A
        assert record.asset == "BTC-USD"
        assert record.favoured_side == "Up"
        assert float(record.bias_ratio) == float(Decimal("2.5"))
        assert record.state == "hedged"
        assert record.leg1_side == "Up"
        assert float(record.leg1_entry) == float(Decimal("0.55"))
        assert float(record.leg1_qty) == float(Decimal("18.18"))
        assert record.hedge_entry is not None
        assert float(record.hedge_entry) == float(Decimal("0.35"))
        assert record.hedge_qty is not None
        assert float(record.hedge_qty) == float(Decimal("18.18"))
        assert record.hedge_side == "Down"
        assert float(record.total_cost_basis) == float(Decimal("16.36"))
        assert record.exit_time == _BASE_TS
        assert record.winning_side == "Up"
        assert float(record.pnl) == float(Decimal("1.50"))
        assert record.is_paper is True
        assert record.order_ids == "order_1,order_2"

    def test_from_copy_result_with_no_hedge(self) -> None:
        """Verify nullable hedge fields are None when no hedge leg."""
        result = CopyResult(
            signal=_make_signal(),
            state=PositionState.UNHEDGED,
            leg1_side="Up",
            leg1_entry=Decimal("0.55"),
            leg1_qty=Decimal("18.18"),
            hedge_entry=None,
            hedge_qty=None,
            total_cost_basis=Decimal("10.00"),
            entry_time=_BASE_TS - 100,
            exit_time=_BASE_TS,
            winning_side="Up",
            pnl=Decimal("8.18"),
            is_paper=True,
        )
        record = CopyResultRecord.from_copy_result(result)

        assert record.hedge_entry is None
        assert record.hedge_qty is None
        assert record.state == "unhedged"
        assert record.order_ids == ""


class TestCopyResultRepository:
    """Tests for the async CopyResultRepository."""

    @pytest_asyncio.fixture
    async def repo(self) -> CopyResultRepository:
        """Create an in-memory SQLite repository for testing."""
        repository = CopyResultRepository("sqlite+aiosqlite:///:memory:")
        await repository.init_db()
        return repository

    @pytest.mark.asyncio
    async def test_save_and_retrieve(self, repo: CopyResultRepository) -> None:
        """Save a record and retrieve it within a time range."""
        result = _make_copy_result()
        record = CopyResultRecord.from_copy_result(result)
        await repo.save_result(record)

        rows = await repo.get_results(_BASE_TS - 1, _BASE_TS + 1)
        assert len(rows) == 1
        assert rows[0].condition_id == _CONDITION_A
        assert float(rows[0].pnl) == float(Decimal("1.50"))

    @pytest.mark.asyncio
    async def test_time_range_filtering(self, repo: CopyResultRepository) -> None:
        """Only return results whose exit_time falls within the range."""
        for i in range(_EXPECTED_RESULT_COUNT_3):
            result = _make_copy_result(
                condition_id=f"cond_{i}",
                exit_time=_BASE_TS + i * 100,
            )
            await repo.save_result(CopyResultRecord.from_copy_result(result))

        # Query for the first two only
        rows = await repo.get_results(_BASE_TS - 1, _BASE_TS + 101)
        assert len(rows) == _EXPECTED_RESULT_COUNT_2

    @pytest.mark.asyncio
    async def test_asset_filter(self, repo: CopyResultRepository) -> None:
        """Filter results by asset."""
        await repo.save_result(
            CopyResultRecord.from_copy_result(_make_copy_result(asset="BTC-USD"))
        )
        await repo.save_result(
            CopyResultRecord.from_copy_result(
                _make_copy_result(condition_id=_CONDITION_B, asset="ETH-USD")
            )
        )

        btc_rows = await repo.get_results(_BASE_TS - 1, _BASE_TS + 1, asset="BTC-USD")
        assert len(btc_rows) == 1
        assert btc_rows[0].asset == "BTC-USD"

        eth_rows = await repo.get_results(_BASE_TS - 1, _BASE_TS + 1, asset="ETH-USD")
        assert len(eth_rows) == 1
        assert eth_rows[0].asset == "ETH-USD"

    @pytest.mark.asyncio
    async def test_paper_filter(self, repo: CopyResultRepository) -> None:
        """Filter results by paper/live mode."""
        await repo.save_result(CopyResultRecord.from_copy_result(_make_copy_result(is_paper=True)))
        await repo.save_result(
            CopyResultRecord.from_copy_result(
                _make_copy_result(condition_id=_CONDITION_B, is_paper=False)
            )
        )

        paper = await repo.get_results(_BASE_TS - 1, _BASE_TS + 1, is_paper=True)
        assert len(paper) == 1
        assert paper[0].is_paper is True

        live = await repo.get_results(_BASE_TS - 1, _BASE_TS + 1, is_paper=False)
        assert len(live) == 1
        assert live[0].is_paper is False

    @pytest.mark.asyncio
    async def test_empty_results(self, repo: CopyResultRepository) -> None:
        """Return an empty list when no results match."""
        rows = await repo.get_results(_BASE_TS - 1, _BASE_TS + 1)
        assert rows == []

    @pytest.mark.asyncio
    async def test_close_disposes_engine(self, repo: CopyResultRepository) -> None:
        """Verify close() disposes the engine without error."""
        await repo.close()
