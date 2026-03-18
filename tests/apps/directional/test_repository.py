"""Tests for the directional result repository."""

from decimal import Decimal

import pytest

from trading_tools.apps.directional.models import (
    DirectionalResult,
    DirectionalResultRecord,
    FeatureVector,
    MarketOpportunity,
)
from trading_tools.apps.directional.repository import DirectionalResultRepository

_WINDOW_START = 1_710_000_000
_WINDOW_END = 1_710_000_300
_ENTRY_TIME = 1_710_000_270
_SETTLED_AT = 1_710_000_300


def _make_result(
    condition_id: str = "cond_1",
    settled_at: int = _SETTLED_AT,
    pnl: Decimal = Decimal("5.00"),
    asset: str = "BTC-USD",
    *,
    is_paper: bool = True,
) -> DirectionalResult:
    """Create a test DirectionalResult."""
    opp = MarketOpportunity(
        condition_id=condition_id,
        title="BTC Up or Down?",
        asset=asset,
        up_token_id="up_tok",
        down_token_id="down_tok",
        window_start_ts=_WINDOW_START,
        window_end_ts=_WINDOW_END,
        up_price=Decimal("0.55"),
        down_price=Decimal("0.45"),
    )
    feat = FeatureVector(
        momentum=Decimal("0.3"),
        volatility_regime=Decimal("-0.1"),
        volume_profile=Decimal("0.2"),
        book_imbalance=Decimal("0.15"),
        rsi_signal=Decimal("0.05"),
        price_change_pct=Decimal("0.25"),
    )
    return DirectionalResult(
        opportunity=opp,
        predicted_side="Up",
        winning_side="Up",
        p_up=Decimal("0.65"),
        token_id="up_tok",
        entry_price=Decimal("0.55"),
        quantity=Decimal(20),
        cost_basis=Decimal("11.02"),
        fee=Decimal("0.02"),
        entry_time=_ENTRY_TIME,
        settled_at=settled_at,
        pnl=pnl,
        features=feat,
        is_paper=is_paper,
    )


class TestDirectionalResultRepository:
    """Test async repository for directional results."""

    @pytest.mark.asyncio
    async def test_init_db_and_save(self) -> None:
        """Initialize DB and save a result without error."""
        repo = DirectionalResultRepository("sqlite+aiosqlite:///:memory:")
        await repo.init_db()
        result = _make_result()
        record = DirectionalResultRecord.from_result(result)
        await repo.save_result(record)
        await repo.close()

    @pytest.mark.asyncio
    async def test_get_results_by_time_range(self) -> None:
        """Query results within a time range."""
        repo = DirectionalResultRepository("sqlite+aiosqlite:///:memory:")
        await repo.init_db()

        for i in range(3):
            result = _make_result(condition_id=f"cond_{i}", settled_at=_SETTLED_AT + i * 100)
            record = DirectionalResultRecord.from_result(result)
            await repo.save_result(record)

        rows = await repo.get_results(_SETTLED_AT, _SETTLED_AT + 150)
        assert len(rows) == 2
        await repo.close()

    @pytest.mark.asyncio
    async def test_filter_by_paper(self) -> None:
        """Filter results by paper/live mode."""
        repo = DirectionalResultRepository("sqlite+aiosqlite:///:memory:")
        await repo.init_db()

        for is_paper in [True, False]:
            result = _make_result(
                condition_id=f"cond_{'paper' if is_paper else 'live'}",
                is_paper=is_paper,
            )
            record = DirectionalResultRecord.from_result(result)
            await repo.save_result(record)

        rows = await repo.get_results(_SETTLED_AT - 1, _SETTLED_AT + 1, is_paper=True)
        assert len(rows) == 1
        assert rows[0].is_paper is True
        await repo.close()

    @pytest.mark.asyncio
    async def test_filter_by_asset(self) -> None:
        """Filter results by asset."""
        repo = DirectionalResultRepository("sqlite+aiosqlite:///:memory:")
        await repo.init_db()

        for asset in ["BTC-USD", "ETH-USD"]:
            result = _make_result(condition_id=f"cond_{asset}", asset=asset)
            record = DirectionalResultRecord.from_result(result)
            await repo.save_result(record)

        rows = await repo.get_results(_SETTLED_AT - 1, _SETTLED_AT + 1, asset="ETH-USD")
        assert len(rows) == 1
        assert rows[0].asset == "ETH-USD"
        await repo.close()
