"""Volume Weighted Average Price (VWAP) strategy.

How it works:
    VWAP is the average price of an asset, weighted by how much was traded
    at each price. Instead of treating every candle equally (like an SMA),
    VWAP gives more importance to candles with higher trading volume.

    The formula over a rolling window of N candles:

        VWAP = sum(close_i * volume_i) / sum(volume_i)

    If a lot of trading happened at $100 and very little at $95, the VWAP
    will be close to $100 even though the simple average would be $97.50.
    VWAP represents the "fair price" that most market participants actually
    traded at.

    The strategy generates:
    - BUY when the current price drops below VWAP. The asset is trading
      below its recent "fair value" -- it may be underpriced.
    - SELL when the current price rises above VWAP. The asset is trading
      above its fair value -- it may be overpriced.

What it tries to achieve:
    Buy cheap, sell expensive relative to what the crowd paid. Large
    institutional traders often use VWAP as a benchmark -- they want to
    buy below VWAP and sell above it. This strategy piggybacks on that
    behavior, betting that prices will revert toward the volume-weighted
    average.

Params:
    period: Number of candles for the rolling VWAP window (default 20).
"""

from collections import deque
from decimal import Decimal

from trading_tools.core.models import ONE, Candle, Side, Signal


class VwapStrategy:
    """Generate BUY when price crosses below VWAP, SELL when above.

    VWAP acts like a "gravity line" for the price. When the price drifts
    below this line, it's considered cheap (buy opportunity). When it floats
    above, it's considered expensive (sell opportunity).
    """

    def __init__(self, period: int = 20) -> None:
        """Initialize the VWAP strategy."""
        if period < 2:  # noqa: PLR2004
            msg = f"period must be >= 2, got {period}"
            raise ValueError(msg)
        self._period = period
        self._close_volume: deque[Decimal] = deque(maxlen=period)
        self._volumes: deque[Decimal] = deque(maxlen=period)
        self._prev_close = Decimal(0)
        self._prev_vwap = Decimal(0)
        self._candle_count = 0

    @property
    def name(self) -> str:
        """Return the strategy name including parameters."""
        return f"vwap_{self._period}"

    def on_candle(self, candle: Candle, history: list[Candle]) -> Signal | None:  # noqa: ARG002
        """Evaluate the candle and return a signal on VWAP crossover."""
        self._close_volume.append(candle.close * candle.volume)
        self._volumes.append(candle.volume)
        self._candle_count += 1

        if self._candle_count < self._period + 1:
            self._prev_close = candle.close
            if self._candle_count >= self._period:
                vwap = self._compute_vwap()
                if vwap is not None:
                    self._prev_vwap = vwap
            return None

        curr_vwap = self._compute_vwap()
        if curr_vwap is None:
            self._prev_close = candle.close
            return None

        prev_close = self._prev_close
        prev_vwap = self._prev_vwap

        self._prev_close = candle.close
        self._prev_vwap = curr_vwap

        if prev_close >= prev_vwap and candle.close < curr_vwap:
            return Signal(
                side=Side.BUY,
                symbol=candle.symbol,
                strength=ONE,
                reason=f"Price crossed below VWAP({self._period})",
            )
        if prev_close <= prev_vwap and candle.close > curr_vwap:
            return Signal(
                side=Side.SELL,
                symbol=candle.symbol,
                strength=ONE,
                reason=f"Price crossed above VWAP({self._period})",
            )
        return None

    def _compute_vwap(self) -> Decimal | None:
        """Compute VWAP from the current window.

        Return ``None`` when total volume is zero, indicating no
        meaningful VWAP can be calculated.
        """
        total_volume = sum(self._volumes)
        if total_volume == 0:
            return None
        return sum(self._close_volume) / total_volume
