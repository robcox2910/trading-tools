"""Market making strategy for prediction markets.

Simulate providing liquidity around the midpoint by maintaining virtual bid
and ask levels. Generate BUY signals when the YES price crosses below the
virtual bid and SELL signals when it crosses above the virtual ask. Track
inventory to prevent over-accumulation.
"""

from decimal import Decimal

from trading_tools.apps.polymarket_bot.models import MarketSnapshot
from trading_tools.core.models import ONE, ZERO, Side, Signal

_TWO = Decimal(2)


class PMMarketMakingStrategy:
    """Simulate a market maker with virtual bid/ask around the midpoint.

    Place virtual orders at ``midpoint - half_spread`` (bid) and
    ``midpoint + half_spread`` (ask). When the market price crosses these
    levels, generate trade signals. Inventory tracking prevents the strategy
    from accumulating more than ``max_inventory`` units.
    """

    def __init__(
        self,
        spread_pct: Decimal = Decimal("0.03"),
        max_inventory: int = 5,
    ) -> None:
        """Initialize the market making strategy.

        Args:
            spread_pct: Half-spread as a fraction of the midpoint (e.g. 0.03 = 3%).
            max_inventory: Maximum net position before stopping buys/sells.

        Raises:
            ValueError: If spread_pct <= 0 or max_inventory < 1.

        """
        if spread_pct <= ZERO:
            msg = f"spread_pct must be > 0, got {spread_pct}"
            raise ValueError(msg)
        if max_inventory < 1:
            msg = f"max_inventory must be >= 1, got {max_inventory}"
            raise ValueError(msg)
        self._spread_pct = spread_pct
        self._max_inventory = max_inventory
        self._inventory = 0
        self._prev_price: Decimal | None = None

    @property
    def name(self) -> str:
        """Return the strategy name including parameters."""
        return f"pm_market_making_{self._spread_pct}_{self._max_inventory}"

    def on_snapshot(
        self,
        snapshot: MarketSnapshot,
        history: list[MarketSnapshot],  # noqa: ARG002
        related: list[MarketSnapshot] | None = None,  # noqa: ARG002
    ) -> Signal | None:
        """Evaluate the snapshot against virtual bid/ask levels.

        Args:
            snapshot: Current market state.
            history: Previous snapshots (unused — internal state tracks price).
            related: Related market snapshots (unused by this strategy).

        Returns:
            A ``Signal`` if the price crosses a virtual level, else ``None``.

        """
        midpoint = (snapshot.yes_price + snapshot.no_price) / _TWO
        half_spread = midpoint * self._spread_pct
        virtual_bid = midpoint - half_spread
        virtual_ask = midpoint + half_spread

        current = snapshot.yes_price
        prev = self._prev_price
        self._prev_price = current

        if prev is None:
            return None

        if prev >= virtual_bid and current < virtual_bid and self._inventory < self._max_inventory:
            self._inventory += 1
            strength = ONE - Decimal(self._inventory) / Decimal(self._max_inventory + 1)
            return Signal(
                side=Side.BUY,
                symbol=snapshot.condition_id,
                strength=max(strength, Decimal("0.1")),
                reason=(
                    f"Price ({current:.4f}) crossed below virtual bid "
                    f"({virtual_bid:.4f}), inventory={self._inventory}"
                ),
            )

        if prev <= virtual_ask and current > virtual_ask and self._inventory > -self._max_inventory:
            self._inventory -= 1
            strength = ONE - Decimal(abs(self._inventory)) / Decimal(self._max_inventory + 1)
            return Signal(
                side=Side.SELL,
                symbol=snapshot.condition_id,
                strength=max(strength, Decimal("0.1")),
                reason=(
                    f"Price ({current:.4f}) crossed above virtual ask "
                    f"({virtual_ask:.4f}), inventory={self._inventory}"
                ),
            )

        return None
