"""Protocol for prediction market trading strategies.

Define the ``PredictionMarketStrategy`` interface that decouples the bot
engine from concrete strategy implementations. Unlike the core
``TradingStrategy`` protocol (candle-based), this protocol operates on
``MarketSnapshot`` objects tailored for binary outcome markets.
"""

from typing import Protocol, runtime_checkable

from trading_tools.apps.polymarket_bot.models import MarketSnapshot
from trading_tools.core.models import Signal


@runtime_checkable
class PredictionMarketStrategy(Protocol):
    """Strategy interface for prediction market trading.

    Implementors receive market snapshots one at a time along with historical
    snapshots and optionally related market snapshots (for cross-market
    strategies). Return a ``Signal`` to indicate a trade action, or ``None``
    to hold.
    """

    @property
    def name(self) -> str:
        """Return the strategy name."""
        ...

    def on_snapshot(
        self,
        snapshot: MarketSnapshot,
        history: list[MarketSnapshot],
        related: list[MarketSnapshot] | None = None,
    ) -> Signal | None:
        """Evaluate a market snapshot and return a trading signal or None.

        Args:
            snapshot: Current market state.
            history: Previous snapshots for this market (oldest first).
            related: Snapshots of related markets for cross-market strategies.

        Returns:
            A ``Signal`` if the strategy detects a trade opportunity, else ``None``.

        """
        ...
