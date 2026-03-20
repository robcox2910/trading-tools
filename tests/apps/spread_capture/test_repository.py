"""Tests for SpreadResultRepository async persistence."""

from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio

from trading_tools.apps.spread_capture.models import (
    PositionState,
    SpreadOpportunity,
    SpreadResult,
    SpreadResultRecord,
)
from trading_tools.apps.spread_capture.repository import SpreadResultRepository

_UP_PRICE = Decimal("0.48")
_DOWN_PRICE = Decimal("0.47")
_COMBINED = _UP_PRICE + _DOWN_PRICE
_MARGIN = Decimal(1) - _COMBINED
_QTY = Decimal(10)
_WINDOW_START = 1_710_000_000
_WINDOW_END = 1_710_000_300
_ENTRY_TIME = 1_710_000_100
_EXIT_TIME = 1_710_000_310


def _make_opportunity(**overrides: Any) -> SpreadOpportunity:
    """Create a SpreadOpportunity with sensible defaults."""
    defaults: dict[str, Any] = {
        "condition_id": "cond_a",
        "title": "Bitcoin Up or Down?",
        "asset": "BTC-USD",
        "up_token_id": "up_tok_1",
        "down_token_id": "down_tok_1",
        "up_price": _UP_PRICE,
        "down_price": _DOWN_PRICE,
        "combined": _COMBINED,
        "margin": _MARGIN,
        "window_start_ts": _WINDOW_START,
        "window_end_ts": _WINDOW_END,
    }
    defaults.update(overrides)
    return SpreadOpportunity(**defaults)


def _make_result(**overrides: Any) -> SpreadResult:
    """Create a SpreadResult with sensible defaults."""
    opp = _make_opportunity(**(overrides.pop("opp_overrides", {}) or {}))
    defaults: dict[str, Any] = {
        "opportunity": opp,
        "state": PositionState.SETTLED,
        "up_entry": _UP_PRICE,
        "up_qty": _QTY,
        "down_entry": _DOWN_PRICE,
        "down_qty": _QTY,
        "total_cost_basis": _COMBINED * _QTY,
        "entry_time": _ENTRY_TIME,
        "exit_time": _EXIT_TIME,
        "winning_side": "Up",
        "pnl": _QTY * _MARGIN,
        "is_paper": True,
        "order_ids": (),
    }
    defaults.update(overrides)
    return SpreadResult(**defaults)


@pytest_asyncio.fixture
async def repo() -> SpreadResultRepository:
    """Create an in-memory SQLite repository."""
    repository = SpreadResultRepository("sqlite+aiosqlite:///:memory:")
    await repository.init_db()
    return repository


@pytest.mark.asyncio
class TestSpreadResultRepository:
    """Test async repository operations."""

    async def test_save_and_query(self, repo: SpreadResultRepository) -> None:
        """Save a result and retrieve it by time range."""
        result = _make_result()
        record = SpreadResultRecord.from_spread_result(result)
        await repo.save_result(record)

        rows = await repo.get_results(_EXIT_TIME - 1, _EXIT_TIME + 1)
        assert len(rows) == 1
        assert rows[0].condition_id == "cond_a"
        assert rows[0].pnl == float(_QTY * _MARGIN)

    async def test_filter_by_paper(self, repo: SpreadResultRepository) -> None:
        """Filter results by paper/live mode."""
        paper = _make_result(is_paper=True)
        live = _make_result(is_paper=False, exit_time=_EXIT_TIME + 1)
        await repo.save_result(SpreadResultRecord.from_spread_result(paper))
        await repo.save_result(SpreadResultRecord.from_spread_result(live))

        paper_rows = await repo.get_results(_EXIT_TIME - 1, _EXIT_TIME + 10, is_paper=True)
        assert len(paper_rows) == 1
        assert paper_rows[0].is_paper is True

    async def test_filter_by_asset(self, repo: SpreadResultRepository) -> None:
        """Filter results by asset."""
        btc = _make_result()
        eth = _make_result(
            opp_overrides={"asset": "ETH-USD", "condition_id": "cond_b"},
            exit_time=_EXIT_TIME + 1,
        )
        await repo.save_result(SpreadResultRecord.from_spread_result(btc))
        await repo.save_result(SpreadResultRecord.from_spread_result(eth))

        rows = await repo.get_results(_EXIT_TIME - 1, _EXIT_TIME + 10, asset="ETH-USD")
        assert len(rows) == 1
        assert rows[0].asset == "ETH-USD"

    async def test_empty_results(self, repo: SpreadResultRepository) -> None:
        """Empty time range returns no results."""
        rows = await repo.get_results(0, 1)
        assert rows == []

    async def test_init_db_idempotent(self, repo: SpreadResultRepository) -> None:
        """Calling init_db twice does not raise."""
        await repo.init_db()
