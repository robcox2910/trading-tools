"""Stochastic oscillator strategy.

How it works:
    The Stochastic oscillator answers one question: "Where is today's
    closing price relative to the recent price range?"

    It produces two lines, both between 0 and 100:

    - %K (the "fast" line):
        %K = (close - lowest_low) / (highest_high - lowest_low) * 100

      If %K = 90, the close is near the top of the recent range (90% of
      the way up). If %K = 10, it's near the bottom.

    - %D (the "slow" line):
        %D = simple moving average of the last few %K values.

      %D smooths out %K so it doesn't jump around as much.

    The strategy generates signals when %K crosses %D, but ONLY in
    extreme zones:
    - BUY when %K crosses above %D while both are in the oversold zone
      (below 20 by default). This means the price was near its recent
      low but is starting to turn up.
    - SELL when %K crosses below %D while both are in the overbought
      zone (above 80 by default). This means the price was near its
      recent high but is starting to turn down.

What it tries to achieve:
    Spot turning points in sideways or range-bound markets. The Stochastic
    works best when the price is bouncing between a floor and a ceiling.
    By only trading in the extreme zones, it avoids acting on meaningless
    mid-range noise.

Params:
    k_period:   Number of candles to look back for the high/low range (default 14).
    d_period:   Number of %K values to average for %D (default 3).
    overbought: %K level above which SELL signals are allowed (default 80).
    oversold:   %K level below which BUY signals are allowed (default 20).
"""

from collections import deque
from decimal import Decimal

from trading_tools.core.models import HUNDRED, ONE, Candle, Side, Signal


class StochasticStrategy:
    """Generate BUY/SELL on %K/%D crossovers in oversold/overbought zones.

    Imagine a thermometer where 0 = coldest recent price and 100 = hottest.
    This strategy buys when the thermometer is very low and starts ticking
    up (oversold + %K crossing above %D), and sells when it's very high
    and starts ticking down (overbought + %K crossing below %D).
    """

    def __init__(
        self,
        k_period: int = 14,
        d_period: int = 3,
        overbought: int = 80,
        oversold: int = 20,
    ) -> None:
        """Initialize the Stochastic oscillator strategy."""
        if k_period < 1:
            msg = f"k_period must be >= 1, got {k_period}"
            raise ValueError(msg)
        if d_period < 1:
            msg = f"d_period must be >= 1, got {d_period}"
            raise ValueError(msg)
        if not (0 < oversold < overbought < 100):  # noqa: PLR2004
            msg = f"Need 0 < oversold ({oversold}) < overbought ({overbought}) < 100"
            raise ValueError(msg)
        self._k_period = k_period
        self._d_period = d_period
        self._overbought = Decimal(overbought)
        self._oversold = Decimal(oversold)

        self._highs: deque[Decimal] = deque(maxlen=k_period)
        self._lows: deque[Decimal] = deque(maxlen=k_period)
        self._closes: deque[Decimal] = deque(maxlen=k_period)
        self._k_values: deque[Decimal] = deque(maxlen=d_period)
        self._prev_k = Decimal(0)
        self._prev_d = Decimal(0)
        self._candle_count = 0

    @property
    def name(self) -> str:
        """Return the strategy name including parameters."""
        return f"stochastic_{self._k_period}_{self._d_period}_{self._oversold}_{self._overbought}"

    def on_candle(self, candle: Candle, history: list[Candle]) -> Signal | None:  # noqa: ARG002
        """Evaluate the candle and return a signal on %K/%D crossover."""
        self._highs.append(candle.high)
        self._lows.append(candle.low)
        self._closes.append(candle.close)
        self._candle_count += 1

        warmup = self._k_period + self._d_period
        if self._candle_count < warmup:
            if self._candle_count >= self._k_period:
                self._k_values.append(self._compute_k())
            return None

        prev_k = self._prev_k
        prev_d = self._prev_d

        curr_k = self._compute_k()
        self._k_values.append(curr_k)
        curr_d = sum(self._k_values) / Decimal(len(self._k_values))

        self._prev_k = curr_k
        self._prev_d = curr_d

        if prev_k <= prev_d and curr_k > curr_d and curr_k < self._oversold:
            return Signal(
                side=Side.BUY,
                symbol=candle.symbol,
                strength=ONE,
                reason=f"%K crossed above %D in oversold zone ({curr_k:.1f})",
            )
        if prev_k >= prev_d and curr_k < curr_d and curr_k > self._overbought:
            return Signal(
                side=Side.SELL,
                symbol=candle.symbol,
                strength=ONE,
                reason=f"%K crossed below %D in overbought zone ({curr_k:.1f})",
            )
        return None

    def _compute_k(self) -> Decimal:
        """Compute %K from the current window."""
        highest = max(self._highs)
        lowest = min(self._lows)
        if highest == lowest:
            return Decimal(50)
        return (self._closes[-1] - lowest) / (highest - lowest) * HUNDRED
