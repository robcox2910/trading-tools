"""Cross-market arbitrage strategy for prediction markets.

Exploit logical inconsistencies between related mutually-exclusive markets.
For a set of mutually exclusive outcomes, the sum of YES prices should equal
approximately 1.0. When the sum deviates, buy the underpriced outcome and
sell the overpriced one.
"""

from decimal import Decimal

from trading_tools.apps.polymarket_bot.models import MarketSnapshot
from trading_tools.core.models import ONE, ZERO, Side, Signal


class PMCrossMarketArbStrategy:
    """Generate signals when related market prices sum away from 1.0.

    For mutually exclusive outcomes (e.g. "Who will win the election?" with
    candidates A, B, C), the sum of YES prices should be approximately 1.0.
    When the sum is significantly above or below 1.0, buy the most
    underpriced outcome or sell the most overpriced one.
    """

    def __init__(self, min_edge: Decimal = Decimal("0.02")) -> None:
        """Initialize the cross-market arbitrage strategy.

        Args:
            min_edge: Minimum mispricing (deviation from fair value) required
                to generate a signal.

        Raises:
            ValueError: If min_edge <= 0.

        """
        if min_edge <= ZERO:
            msg = f"min_edge must be > 0, got {min_edge}"
            raise ValueError(msg)
        self._min_edge = min_edge

    @property
    def name(self) -> str:
        """Return the strategy name including parameters."""
        return f"pm_cross_market_arb_{self._min_edge}"

    def on_snapshot(
        self,
        snapshot: MarketSnapshot,
        history: list[MarketSnapshot],  # noqa: ARG002
        related: list[MarketSnapshot] | None = None,
    ) -> Signal | None:
        """Evaluate the snapshot against related markets for arbitrage.

        Compare the current market's YES price against related markets to
        find mispricing. The sum of all mutually-exclusive YES prices should
        be approximately 1.0.

        Args:
            snapshot: Current market state.
            history: Previous snapshots (unused).
            related: Snapshots of related mutually-exclusive markets.

        Returns:
            A ``Signal`` if a mispricing opportunity is detected, else ``None``.

        """
        if not related:
            return None

        all_snapshots = [snapshot, *related]
        total_yes = sum((s.yes_price for s in all_snapshots), ZERO)

        if total_yes == ZERO:
            return None

        fair_price = snapshot.yes_price / total_yes
        edge = fair_price - snapshot.yes_price

        if abs(edge) < self._min_edge:
            return None

        if edge > ZERO:
            return Signal(
                side=Side.BUY,
                symbol=snapshot.condition_id,
                strength=min(abs(edge) * Decimal(10), ONE),
                reason=(
                    f"Underpriced: fair={fair_price:.4f} vs market="
                    f"{snapshot.yes_price:.4f}, sum={total_yes:.4f}"
                ),
            )

        return Signal(
            side=Side.SELL,
            symbol=snapshot.condition_id,
            strength=min(abs(edge) * Decimal(10), ONE),
            reason=(
                f"Overpriced: fair={fair_price:.4f} vs market="
                f"{snapshot.yes_price:.4f}, sum={total_yes:.4f}"
            ),
        )
