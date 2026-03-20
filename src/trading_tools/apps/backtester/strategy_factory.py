"""Strategy registry and factory for the backtester.

Provide a central list of available strategy names and a factory
function that builds concrete ``TradingStrategy`` instances from
CLI parameters. Both the single-run and comparison modules import
from here to avoid circular dependencies.
"""

from typing import TYPE_CHECKING

import typer

from trading_tools.apps.backtester.strategies.bollinger import BollingerStrategy
from trading_tools.apps.backtester.strategies.buy_and_hold import BuyAndHoldStrategy
from trading_tools.apps.backtester.strategies.donchian import DonchianStrategy
from trading_tools.apps.backtester.strategies.ema_crossover import (
    EmaCrossoverStrategy,
)
from trading_tools.apps.backtester.strategies.macd import MacdStrategy
from trading_tools.apps.backtester.strategies.mean_reversion import (
    MeanReversionStrategy,
)
from trading_tools.apps.backtester.strategies.rsi import RsiStrategy
from trading_tools.apps.backtester.strategies.sma_crossover import (
    SmaCrossoverStrategy,
)
from trading_tools.apps.backtester.strategies.stochastic import StochasticStrategy
from trading_tools.apps.backtester.strategies.vwap import VwapStrategy
from trading_tools.core.protocols import TradingStrategy

if TYPE_CHECKING:
    from collections.abc import Callable

STRATEGY_NAMES = (
    "sma_crossover",
    "ema_crossover",
    "rsi",
    "bollinger",
    "macd",
    "stochastic",
    "vwap",
    "donchian",
    "mean_reversion",
    "buy_and_hold",
)


def build_strategy(
    name: str,
    *,
    short_period: int,
    long_period: int,
    period: int,
    overbought: int,
    oversold: int,
    num_std: float,
    fast_period: int,
    slow_period: int,
    signal_period: int,
    k_period: int,
    d_period: int,
    z_threshold: float,
) -> TradingStrategy:
    """Build a strategy instance from CLI parameters.

    Use a dictionary dispatch to map the strategy name to a concrete
    ``TradingStrategy`` implementation, passing through the relevant
    subset of CLI parameters for each strategy type.

    Args:
        name: Strategy identifier (must be one of ``STRATEGY_NAMES``).
        short_period: Short period for SMA/EMA crossover strategies.
        long_period: Long period for SMA/EMA crossover strategies.
        period: Period for RSI, Bollinger, VWAP, Donchian, Mean Reversion.
        overbought: Overbought threshold for RSI/Stochastic.
        oversold: Oversold threshold for RSI/Stochastic.
        num_std: Standard deviations for Bollinger Bands.
        fast_period: MACD fast EMA period.
        slow_period: MACD slow EMA period.
        signal_period: MACD signal EMA period.
        k_period: Stochastic %K period.
        d_period: Stochastic %D period.
        z_threshold: Mean reversion z-score threshold.

    Returns:
        A configured ``TradingStrategy`` instance.

    Raises:
        typer.BadParameter: If the strategy name is not recognised.

    """
    builders: dict[str, Callable[[], TradingStrategy]] = {
        "sma_crossover": lambda: SmaCrossoverStrategy(short_period, long_period),
        "ema_crossover": lambda: EmaCrossoverStrategy(short_period, long_period),
        "rsi": lambda: RsiStrategy(period=period, overbought=overbought, oversold=oversold),
        "bollinger": lambda: BollingerStrategy(period=period, num_std=num_std),
        "macd": lambda: MacdStrategy(
            fast_period=fast_period,
            slow_period=slow_period,
            signal_period=signal_period,
        ),
        "stochastic": lambda: StochasticStrategy(
            k_period=k_period,
            d_period=d_period,
            overbought=overbought,
            oversold=oversold,
        ),
        "vwap": lambda: VwapStrategy(period=period),
        "donchian": lambda: DonchianStrategy(period=period),
        "mean_reversion": lambda: MeanReversionStrategy(period=period, z_threshold=z_threshold),
        "buy_and_hold": BuyAndHoldStrategy,
    }
    if name not in builders:
        msg = f"Unknown strategy: {name}"
        raise typer.BadParameter(msg)
    return builders[name]()
