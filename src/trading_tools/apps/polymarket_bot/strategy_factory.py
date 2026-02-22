"""Strategy registry and factory for the Polymarket paper trading bot.

Provide a central list of available prediction market strategy names and a
factory function that builds concrete ``PredictionMarketStrategy`` instances
from CLI parameters.
"""

from decimal import Decimal
from typing import Any

import typer

from trading_tools.apps.polymarket_bot.protocols import PredictionMarketStrategy
from trading_tools.apps.polymarket_bot.strategies.cross_market_arb import (
    PMCrossMarketArbStrategy,
)
from trading_tools.apps.polymarket_bot.strategies.late_snipe import (
    PMLateSnipeStrategy,
)
from trading_tools.apps.polymarket_bot.strategies.liquidity_imbalance import (
    PMLiquidityImbalanceStrategy,
)
from trading_tools.apps.polymarket_bot.strategies.market_making import (
    PMMarketMakingStrategy,
)
from trading_tools.apps.polymarket_bot.strategies.mean_reversion import (
    PMMeanReversionStrategy,
)

PM_STRATEGY_NAMES = (
    "pm_mean_reversion",
    "pm_market_making",
    "pm_liquidity_imbalance",
    "pm_cross_market_arb",
    "pm_late_snipe",
)


def build_pm_strategy(name: str, **kwargs: Any) -> PredictionMarketStrategy:
    """Build a prediction market strategy instance from a name and parameters.

    Use dictionary dispatch to map the strategy name to a concrete
    implementation, forwarding relevant keyword arguments.

    Args:
        name: Strategy identifier (must be one of ``PM_STRATEGY_NAMES``).
        **kwargs: Strategy-specific parameters. Supported keys:
            - ``period``: Rolling window size for mean reversion.
            - ``z_threshold``: Z-score threshold for mean reversion.
            - ``spread_pct``: Half-spread for market making.
            - ``max_inventory``: Max inventory for market making.
            - ``imbalance_threshold``: Threshold for liquidity imbalance.
            - ``depth_levels``: Order book depth for liquidity imbalance.
            - ``min_edge``: Minimum edge for cross-market arbitrage.

    Returns:
        A configured ``PredictionMarketStrategy`` instance.

    Raises:
        typer.BadParameter: If the strategy name is not recognised.

    """
    builders: dict[str, PredictionMarketStrategy] = {
        "pm_mean_reversion": PMMeanReversionStrategy(
            period=kwargs.get("period", 20),
            z_threshold=Decimal(str(kwargs.get("z_threshold", "1.5"))),
        ),
        "pm_market_making": PMMarketMakingStrategy(
            spread_pct=Decimal(str(kwargs.get("spread_pct", "0.03"))),
            max_inventory=kwargs.get("max_inventory", 5),
        ),
        "pm_liquidity_imbalance": PMLiquidityImbalanceStrategy(
            imbalance_threshold=Decimal(str(kwargs.get("imbalance_threshold", "0.65"))),
            depth_levels=kwargs.get("depth_levels", 5),
        ),
        "pm_cross_market_arb": PMCrossMarketArbStrategy(
            min_edge=Decimal(str(kwargs.get("min_edge", "0.02"))),
        ),
        "pm_late_snipe": PMLateSnipeStrategy(
            threshold=Decimal(str(kwargs.get("snipe_threshold", "0.90"))),
            window_seconds=kwargs.get("snipe_window", 60),
        ),
    }
    if name not in builders:
        msg = f"Unknown strategy: {name}. Available: {', '.join(PM_STRATEGY_NAMES)}"
        raise typer.BadParameter(msg)
    return builders[name]
