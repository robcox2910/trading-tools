"""Mean reversion strategy based on z-score.

How it works:
    This strategy is built on a simple idea: prices tend to return to their
    average over time. If a stock usually trades around $100 and suddenly
    drops to $80, it will often bounce back toward $100 eventually.

    The strategy uses a "z-score" to measure how far the current price has
    drifted from its recent average. The z-score tells you how many
    standard deviations away the price is:

        z-score = (current_price - average_price) / standard_deviation

    - z-score of 0   = price is exactly at the average (normal).
    - z-score of +2  = price is 2 standard deviations ABOVE average
                        (unusually high -- roughly top 2.5% of values).
    - z-score of -2  = price is 2 standard deviations BELOW average
                        (unusually low -- roughly bottom 2.5% of values).

    The strategy generates:
    - BUY when the z-score drops below -threshold (e.g. -2.0). The price
      has fallen unusually far below its average and is likely to bounce
      back up.
    - SELL when the z-score rises above +threshold (e.g. +2.0). The price
      has risen unusually far above its average and is likely to drop back
      down.

What it tries to achieve:
    Profit from the "rubber band" effect in prices. Extreme moves away
    from the average are often temporary. This strategy buys at the
    stretched-low point and sells at the stretched-high point, expecting
    the rubber band to snap back. It works best in range-bound markets
    and can lose money in strong trends where prices keep moving away
    from the mean.

Params:
    period:      Number of candles for calculating the rolling average
                 and standard deviation (default 20).
    z_threshold: How many standard deviations from the mean triggers a
                 signal (default 2.0).
"""

from collections import deque
from decimal import Decimal

from trading_tools.core.models import ONE, Candle, Side, Signal


class MeanReversionStrategy:
    """Generate BUY when z-score drops below -threshold, SELL when above +threshold.

    Think of a dog on a leash (the average price). The dog can run ahead
    or lag behind, but the leash always pulls it back. This strategy buys
    when the dog is far behind (z-score very negative) and sells when the
    dog has run far ahead (z-score very positive).
    """

    def __init__(self, period: int = 20, z_threshold: float = 2.0) -> None:
        """Initialize the mean reversion strategy."""
        if period < 2:  # noqa: PLR2004
            msg = f"period must be >= 2, got {period}"
            raise ValueError(msg)
        if z_threshold <= 0:
            msg = f"z_threshold must be > 0, got {z_threshold}"
            raise ValueError(msg)
        self._period = period
        self._z_threshold = Decimal(str(z_threshold))
        self._closes: deque[Decimal] = deque(maxlen=period)
        self._prev_z: Decimal = Decimal(0)
        self._candle_count = 0

    @property
    def name(self) -> str:
        """Return the strategy name including parameters."""
        return f"mean_reversion_{self._period}_{self._z_threshold}"

    def on_candle(self, candle: Candle, history: list[Candle]) -> Signal | None:  # noqa: ARG002
        """Evaluate the candle and return a signal based on z-score thresholds."""
        self._closes.append(candle.close)
        self._candle_count += 1

        if self._candle_count < self._period + 1:
            if self._candle_count >= self._period:
                self._prev_z = self._compute_z()
            return None

        curr_z = self._compute_z()
        prev_z = self._prev_z
        self._prev_z = curr_z

        if prev_z >= -self._z_threshold and curr_z < -self._z_threshold:
            return Signal(
                side=Side.BUY,
                symbol=candle.symbol,
                strength=ONE,
                reason=f"Z-score ({curr_z:.2f}) crossed below -{self._z_threshold}",
            )
        if prev_z <= self._z_threshold and curr_z > self._z_threshold:
            return Signal(
                side=Side.SELL,
                symbol=candle.symbol,
                strength=ONE,
                reason=f"Z-score ({curr_z:.2f}) crossed above {self._z_threshold}",
            )
        return None

    def _compute_z(self) -> Decimal:
        """Compute z-score of the latest close relative to the rolling window."""
        closes = list(self._closes)
        mean = sum(closes) / Decimal(len(closes))
        variance = sum((c - mean) ** 2 for c in closes) / Decimal(len(closes))
        if variance == 0:
            return Decimal(0)
        std = variance.sqrt()
        return (closes[-1] - mean) / std
