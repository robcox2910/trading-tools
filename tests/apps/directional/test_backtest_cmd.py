"""Tests for the directional backtest CLI helper functions."""

from decimal import Decimal

from trading_tools.apps.directional.backtest_runner import DirectionalBacktestResult
from trading_tools.apps.polymarket.cli.directional_backtest_cmd import _display_result
from trading_tools.core.models import ZERO


class TestDisplayResult:
    """Test the CLI display helper."""

    def test_display_result_does_not_raise(self) -> None:
        """Display a result without errors."""
        result = DirectionalBacktestResult(
            initial_capital=Decimal(1000),
            final_capital=Decimal(1100),
            total_pnl=Decimal(100),
            return_pct=Decimal(10),
            total_windows=100,
            total_trades=50,
            wins=40,
            losses=10,
            skipped=50,
            win_rate=Decimal("0.80"),
            avg_pnl=Decimal(2),
            brier_score=Decimal("0.20"),
            avg_p_when_correct=Decimal("0.60"),
            avg_p_when_incorrect=Decimal("0.55"),
        )
        _display_result(result)

    def test_display_result_zero_win_rate(self) -> None:
        """Display a result with zero win rate without errors."""
        result = DirectionalBacktestResult(
            initial_capital=Decimal(1000),
            final_capital=Decimal(1000),
            total_pnl=ZERO,
            return_pct=ZERO,
            total_windows=10,
            total_trades=0,
            wins=0,
            losses=0,
            skipped=10,
            win_rate=ZERO,
            avg_pnl=ZERO,
            brier_score=ZERO,
            avg_p_when_correct=ZERO,
            avg_p_when_incorrect=ZERO,
        )
        _display_result(result)
