"""Mean reversion strategy for prediction markets.

Fade YES price deviations from the rolling average. In prediction markets,
prices represent slowly-changing probabilities, so short-term spikes are
typically noise. Buy when the z-score drops below -threshold (price too low)
and sell when it rises above +threshold (price too high).
"""

from collections import deque
from decimal import Decimal

from trading_tools.apps.polymarket_bot.models import MarketSnapshot
from trading_tools.core.models import ONE, ZERO, Side, Signal

_MIN_PERIOD = 2


class PMMeanReversionStrategy:
    """Generate signals when YES price deviates from its rolling mean.

    Compute a rolling mean and standard deviation of ``yes_price`` over a
    configurable number of snapshots. The z-score measures how many standard
    deviations the current price is from the mean. A z-score crossing below
    ``-z_threshold`` triggers a BUY (price is unusually low), while a crossing
    above ``+z_threshold`` triggers a SELL (price is unusually high).
    """

    def __init__(self, period: int = 20, z_threshold: Decimal = Decimal("1.5")) -> None:
        """Initialize the prediction market mean reversion strategy.

        Args:
            period: Number of snapshots for the rolling window (minimum 2).
            z_threshold: Z-score threshold for signal generation (must be > 0).

        Raises:
            ValueError: If period < 2 or z_threshold <= 0.

        """
        if period < _MIN_PERIOD:
            msg = f"period must be >= 2, got {period}"
            raise ValueError(msg)
        if z_threshold <= ZERO:
            msg = f"z_threshold must be > 0, got {z_threshold}"
            raise ValueError(msg)
        self._period = period
        self._z_threshold = z_threshold
        self._prices: deque[Decimal] = deque(maxlen=period)
        self._prev_z: Decimal = ZERO
        self._snapshot_count = 0

    @property
    def name(self) -> str:
        """Return the strategy name including parameters."""
        return f"pm_mean_reversion_{self._period}_{self._z_threshold}"

    def on_snapshot(
        self,
        snapshot: MarketSnapshot,
        history: list[MarketSnapshot],  # noqa: ARG002
        related: list[MarketSnapshot] | None = None,  # noqa: ARG002
    ) -> Signal | None:
        """Evaluate the snapshot's YES price and return a z-score-based signal.

        Args:
            snapshot: Current market state.
            history: Previous snapshots (unused — internal deque tracks prices).
            related: Related market snapshots (unused by this strategy).

        Returns:
            A ``Signal`` if the z-score crosses the threshold, else ``None``.

        """
        self._prices.append(snapshot.yes_price)
        self._snapshot_count += 1

        if self._snapshot_count < self._period + 1:
            if self._snapshot_count >= self._period:
                self._prev_z = self._compute_z()
            return None

        curr_z = self._compute_z()
        prev_z = self._prev_z
        self._prev_z = curr_z

        if prev_z >= -self._z_threshold and curr_z < -self._z_threshold:
            return Signal(
                side=Side.BUY,
                symbol=snapshot.condition_id,
                strength=ONE,
                reason=f"Z-score ({curr_z:.2f}) crossed below -{self._z_threshold}",
            )
        if prev_z <= self._z_threshold and curr_z > self._z_threshold:
            return Signal(
                side=Side.SELL,
                symbol=snapshot.condition_id,
                strength=ONE,
                reason=f"Z-score ({curr_z:.2f}) crossed above {self._z_threshold}",
            )
        return None

    def _compute_z(self) -> Decimal:
        """Compute the z-score of the latest price relative to the rolling window."""
        prices = list(self._prices)
        n = Decimal(len(prices))
        mean = sum(prices) / n
        variance = sum((p - mean) ** Decimal(2) for p in prices) / n
        if variance == ZERO:
            return ZERO
        std = variance.sqrt()
        return (prices[-1] - mean) / std
