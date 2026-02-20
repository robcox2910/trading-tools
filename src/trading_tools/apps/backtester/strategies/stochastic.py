"""Stochastic oscillator strategy."""

from collections import deque
from decimal import Decimal

from trading_tools.core.models import Candle, Side, Signal

ZERO = Decimal(0)
ONE = Decimal(1)
HUNDRED = Decimal(100)


class StochasticStrategy:
    """Generate BUY/SELL on %K/%D crossovers in oversold/overbought zones."""

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
