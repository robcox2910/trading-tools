"""Mean reversion strategy based on z-score."""

from collections import deque
from decimal import Decimal

from trading_tools.core.models import Candle, Side, Signal

ONE = Decimal(1)


class MeanReversionStrategy:
    """Generate BUY when z-score drops below -threshold, SELL when above +threshold."""

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
