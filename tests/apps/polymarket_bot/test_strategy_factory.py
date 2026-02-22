"""Tests for prediction market strategy factory."""

import pytest
import typer

from trading_tools.apps.polymarket_bot.protocols import PredictionMarketStrategy
from trading_tools.apps.polymarket_bot.strategy_factory import (
    PM_STRATEGY_NAMES,
    build_pm_strategy,
)


class TestBuildPmStrategy:
    """Tests for the build_pm_strategy factory function."""

    def test_all_names_build_successfully(self) -> None:
        """Test that all registered strategy names produce valid instances."""
        for name in PM_STRATEGY_NAMES:
            strategy = build_pm_strategy(name)
            assert isinstance(strategy, PredictionMarketStrategy)

    def test_unknown_name_raises(self) -> None:
        """Test that an unknown strategy name raises BadParameter."""
        with pytest.raises(typer.BadParameter, match="Unknown strategy"):
            build_pm_strategy("nonexistent_strategy")

    def test_mean_reversion_custom_params(self) -> None:
        """Test building mean reversion with custom parameters."""
        strategy = build_pm_strategy("pm_mean_reversion", period=10, z_threshold=2.0)
        assert "10" in strategy.name
        assert "2.0" in strategy.name

    def test_market_making_custom_params(self) -> None:
        """Test building market making with custom parameters."""
        strategy = build_pm_strategy("pm_market_making", spread_pct=0.05, max_inventory=3)
        assert "0.05" in strategy.name
        assert "3" in strategy.name

    def test_liquidity_imbalance_custom_params(self) -> None:
        """Test building liquidity imbalance with custom parameters."""
        strategy = build_pm_strategy(
            "pm_liquidity_imbalance", imbalance_threshold=0.70, depth_levels=3
        )
        assert "0.7" in strategy.name

    def test_cross_market_arb_custom_params(self) -> None:
        """Test building cross-market arb with custom parameters."""
        strategy = build_pm_strategy("pm_cross_market_arb", min_edge=0.05)
        assert "0.05" in strategy.name

    def test_strategy_names_tuple_has_four_entries(self) -> None:
        """Test that PM_STRATEGY_NAMES contains exactly four strategies."""
        expected_count = 4
        assert len(PM_STRATEGY_NAMES) == expected_count
