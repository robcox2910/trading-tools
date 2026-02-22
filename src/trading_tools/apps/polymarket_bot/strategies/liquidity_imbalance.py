"""Liquidity imbalance strategy for prediction markets.

Trade based on bid/ask size asymmetry in the order book. When buy-side
liquidity significantly outweighs sell-side liquidity (or vice versa),
the price is likely to move in the direction of the imbalance.
"""

from decimal import Decimal

from trading_tools.apps.polymarket_bot.models import MarketSnapshot
from trading_tools.core.models import ONE, ZERO, Side, Signal


class PMLiquidityImbalanceStrategy:
    """Generate signals based on order book bid/ask size asymmetry.

    Compute the imbalance ratio as ``total_bid_size / (total_bid_size +
    total_ask_size)`` using the top N levels of the order book. An imbalance
    above the threshold indicates heavy buy pressure (BUY signal), while an
    imbalance below ``1 - threshold`` indicates heavy sell pressure (SELL signal).
    """

    def __init__(
        self,
        imbalance_threshold: Decimal = Decimal("0.65"),
        depth_levels: int = 5,
    ) -> None:
        """Initialize the liquidity imbalance strategy.

        Args:
            imbalance_threshold: Ratio above which buy pressure triggers a BUY
                signal. Sell threshold is ``1 - imbalance_threshold``.
            depth_levels: Number of order book levels to consider on each side.

        Raises:
            ValueError: If threshold is not in (0.5, 1.0) or depth_levels < 1.

        """
        if not (Decimal("0.5") < imbalance_threshold < ONE):
            msg = f"imbalance_threshold must be in (0.5, 1.0), got {imbalance_threshold}"
            raise ValueError(msg)
        if depth_levels < 1:
            msg = f"depth_levels must be >= 1, got {depth_levels}"
            raise ValueError(msg)
        self._threshold = imbalance_threshold
        self._depth_levels = depth_levels

    @property
    def name(self) -> str:
        """Return the strategy name including parameters."""
        return f"pm_liquidity_imbalance_{self._threshold}_{self._depth_levels}"

    def on_snapshot(
        self,
        snapshot: MarketSnapshot,
        history: list[MarketSnapshot],  # noqa: ARG002
        related: list[MarketSnapshot] | None = None,  # noqa: ARG002
    ) -> Signal | None:
        """Evaluate the order book imbalance and return a signal.

        Args:
            snapshot: Current market state with order book.
            history: Previous snapshots (unused).
            related: Related market snapshots (unused).

        Returns:
            A ``Signal`` if the imbalance exceeds the threshold, else ``None``.

        """
        book = snapshot.order_book
        bids = book.bids[: self._depth_levels]
        asks = book.asks[: self._depth_levels]

        total_bid = sum((level.size for level in bids), ZERO)
        total_ask = sum((level.size for level in asks), ZERO)
        total = total_bid + total_ask

        if total == ZERO:
            return None

        imbalance = total_bid / total

        if imbalance > self._threshold:
            return Signal(
                side=Side.BUY,
                symbol=snapshot.condition_id,
                strength=min(imbalance, ONE),
                reason=(
                    f"Bid imbalance {imbalance:.2%} > {self._threshold:.2%} "
                    f"(bid={total_bid}, ask={total_ask})"
                ),
            )

        sell_threshold = ONE - self._threshold
        if imbalance < sell_threshold:
            return Signal(
                side=Side.SELL,
                symbol=snapshot.condition_id,
                strength=min(ONE - imbalance, ONE),
                reason=(
                    f"Ask imbalance {ONE - imbalance:.2%} > {self._threshold:.2%} "
                    f"(bid={total_bid}, ask={total_ask})"
                ),
            )

        return None
