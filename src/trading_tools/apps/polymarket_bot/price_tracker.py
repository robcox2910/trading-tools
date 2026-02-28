"""Real-time price tracker for WebSocket-driven trading engines.

Maintain the latest YES/NO token prices per condition ID, updated from
``last_trade_price`` WebSocket events. The tracker maps asset IDs (which
arrive on the WebSocket) to condition IDs (which the engine operates on),
so the engine can look up current prices without polling the HTTP API.
"""

from decimal import Decimal

_YES_INDEX = 0
_NO_INDEX = 1


class PriceTracker:
    """Track real-time YES/NO prices per condition ID from WebSocket events.

    Map asset IDs to condition IDs and token indices so that incoming
    ``last_trade_price`` events (keyed by asset ID) update the correct
    price slot. The engine queries prices by condition ID.

    Example::

        tracker = PriceTracker()
        tracker.register_market("cond_1", "yes_asset", "no_asset")
        tracker.update("yes_asset", Decimal("0.65"))
        prices = tracker.get_prices("cond_1")  # (Decimal("0.65"), None)

    """

    def __init__(self) -> None:
        """Initialize empty price and mapping state."""
        self._asset_to_condition: dict[str, tuple[str, int]] = {}
        self._prices: dict[str, list[Decimal | None]] = {}

    def register_market(
        self,
        condition_id: str,
        yes_asset_id: str,
        no_asset_id: str,
    ) -> None:
        """Register the asset-to-condition mapping for a market.

        Each binary market has two tokens (YES at index 0, NO at index 1).
        Register both so that WebSocket events for either token update the
        correct price slot.

        Args:
            condition_id: Market condition identifier.
            yes_asset_id: Token ID for the YES outcome.
            no_asset_id: Token ID for the NO outcome.

        """
        self._asset_to_condition[yes_asset_id] = (condition_id, _YES_INDEX)
        self._asset_to_condition[no_asset_id] = (condition_id, _NO_INDEX)
        if condition_id not in self._prices:
            self._prices[condition_id] = [None, None]

    def update(self, asset_id: str, price: Decimal) -> str | None:
        """Update the price for a token from a WebSocket trade event.

        Args:
            asset_id: Token identifier from the WebSocket event.
            price: Last trade price.

        Returns:
            The condition ID that was updated, or ``None`` if the asset ID
            is not registered.

        """
        mapping = self._asset_to_condition.get(asset_id)
        if mapping is None:
            return None
        condition_id, token_index = mapping
        self._prices[condition_id][token_index] = price
        return condition_id

    def get_prices(self, condition_id: str) -> tuple[Decimal | None, Decimal | None] | None:
        """Return the latest (yes_price, no_price) for a condition ID.

        Args:
            condition_id: Market condition identifier.

        Returns:
            Tuple of ``(yes_price, no_price)`` where either may be ``None``
            if no trade has been received yet, or ``None`` if the condition
            ID is not registered.

        """
        prices = self._prices.get(condition_id)
        if prices is None:
            return None
        return (prices[_YES_INDEX], prices[_NO_INDEX])

    def clear(self) -> None:
        """Reset all price and mapping state.

        Call on market rotation to discard stale data before registering
        new markets.
        """
        self._asset_to_condition.clear()
        self._prices.clear()
