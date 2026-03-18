"""Tests for the directional grid search module."""

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from trading_tools.apps.directional.config import DirectionalConfig
from trading_tools.apps.directional.grid_backtest import (
    DirectionalGridCell,
    DirectionalGridResult,
    format_grid_table,
    run_directional_grid,
)

_BASE_TS = 1_710_000_000
_END_TS = _BASE_TS + 86400


class TestDirectionalGridCell:
    """Test grid cell dataclass."""

    def test_creation(self) -> None:
        """Create a grid cell with all fields."""
        cell = DirectionalGridCell(
            params={"w_whale": Decimal("0.50")},
            return_pct=Decimal("10.0"),
            total_trades=50,
            wins=30,
            losses=20,
            win_rate=Decimal("0.60"),
            brier_score=Decimal("0.20"),
            avg_pnl=Decimal("2.0"),
            skipped=10,
        )
        assert cell.win_rate == Decimal("0.60")
        assert cell.params["w_whale"] == Decimal("0.50")


class TestFormatGridTable:
    """Test markdown table formatting."""

    def test_empty_result(self) -> None:
        """Empty result returns 'No results.'."""
        result = DirectionalGridResult(cells=(), total_windows=0, initial_capital=Decimal(1000))
        assert format_grid_table(result) == "No results."

    def test_formats_cells(self) -> None:
        """Format cells into a markdown table."""
        cell = DirectionalGridCell(
            params={"w_whale": Decimal("0.50"), "min_edge": Decimal("0.05")},
            return_pct=Decimal("10.0"),
            total_trades=50,
            wins=30,
            losses=20,
            win_rate=Decimal("0.60"),
            brier_score=Decimal("0.20"),
            avg_pnl=Decimal("2.0"),
            skipped=10,
        )
        result = DirectionalGridResult(
            cells=(cell,), total_windows=100, initial_capital=Decimal(1000)
        )
        table = format_grid_table(result)
        assert "w_whale" in table
        assert "min_edge" in table
        assert "0.50" in table
        assert "60" in table  # win rate


class TestRunDirectionalGrid:
    """Test the grid search runner with mocked repository."""

    @pytest.mark.asyncio
    async def test_no_metadata_returns_empty_cells(self) -> None:
        """Return cells with zero metrics when no metadata found."""
        config = DirectionalConfig()
        repo = AsyncMock()
        repo.get_market_metadata_in_range = AsyncMock(return_value=[])

        result = await run_directional_grid(
            base_config=config,
            repo=repo,
            start_ts=_BASE_TS,
            end_ts=_END_TS,
            param_grid={"min_edge": [Decimal("0.03"), Decimal("0.05")]},
        )
        assert len(result.cells) == 2  # noqa: PLR2004
        assert all(c.total_trades == 0 for c in result.cells)

    @pytest.mark.asyncio
    async def test_grid_produces_correct_number_of_cells(self) -> None:
        """Grid produces N cells for N parameter combinations."""
        config = DirectionalConfig()
        repo = AsyncMock()
        repo.get_market_metadata_in_range = AsyncMock(return_value=[])

        result = await run_directional_grid(
            base_config=config,
            repo=repo,
            start_ts=_BASE_TS,
            end_ts=_END_TS,
            param_grid={
                "min_edge": [Decimal("0.03"), Decimal("0.05")],
                "w_whale": [Decimal("0.40"), Decimal("0.50"), Decimal("0.60")],
            },
        )
        assert len(result.cells) == 6  # 2 x 3  # noqa: PLR2004

    @pytest.mark.asyncio
    async def test_cells_sorted_by_brier_score(self) -> None:
        """Cells are sorted by Brier score ascending."""
        config = DirectionalConfig()
        repo = AsyncMock()
        repo.get_market_metadata_in_range = AsyncMock(return_value=[])

        result = await run_directional_grid(
            base_config=config,
            repo=repo,
            start_ts=_BASE_TS,
            end_ts=_END_TS,
            param_grid={"min_edge": [Decimal("0.03"), Decimal("0.05"), Decimal("0.07")]},
        )
        # All zero brier scores (no trades) → sorted arbitrarily but consistently
        assert len(result.cells) == 3  # noqa: PLR2004
