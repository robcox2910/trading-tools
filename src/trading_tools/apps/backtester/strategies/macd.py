"""Moving Average Convergence Divergence (MACD) strategy.

How it works:
    MACD combines two ideas: trend detection and momentum measurement.

    1. Compute a fast EMA (e.g. 12-period) and a slow EMA (e.g. 26-period).
    2. MACD line = fast EMA - slow EMA. When the fast EMA is above the
       slow EMA, the MACD line is positive (upward momentum). When below,
       it's negative (downward momentum).
    3. Signal line = an EMA of the MACD line itself (e.g. 9-period). This
       smooths out the MACD so you don't react to every tiny wiggle.

    The strategy generates a BUY when the MACD line crosses above the
    signal line (momentum is accelerating upward) and a SELL when the
    MACD line crosses below the signal line (momentum is fading or
    reversing).

    Think of it like this: the MACD line is the "speedometer" showing how
    fast the price is moving. The signal line is a smoothed version of
    that speedometer. When the speedometer jumps above its smoothed line,
    the car (price) is accelerating -- time to buy. When it drops below,
    the car is slowing down -- time to sell.

What it tries to achieve:
    Identify changes in the strength, direction, and duration of a trend.
    MACD is one of the most popular indicators because it works in both
    trending and moderately choppy markets. It catches trends earlier than
    a simple moving average crossover because it measures the *gap* between
    two averages, not just which one is higher.

Performance note:
    After the initial warm-up, this strategy updates three EMAs
    incrementally (O(1) per candle) instead of recalculating the full
    MACD series from scratch.

Params:
    fast_period:   Period for the fast EMA (default 12).
    slow_period:   Period for the slow EMA (default 26).
    signal_period: Period for the signal line EMA (default 9).
"""

from decimal import Decimal

from trading_tools.apps.backtester.indicators import ema_from_values
from trading_tools.core.models import ONE, TWO, Candle, Side, Signal


class MacdStrategy:
    """Generate BUY when the MACD line crosses above the signal line, SELL when below.

    The MACD line shows momentum (is the price speeding up or slowing
    down?). The signal line smooths it out. Crossovers between the two
    indicate shifts in momentum direction.
    """

    def __init__(
        self,
        fast_period: int = 12,
        slow_period: int = 26,
        signal_period: int = 9,
    ) -> None:
        """Initialize the MACD strategy."""
        if fast_period >= slow_period:
            msg = f"fast_period ({fast_period}) must be < slow_period ({slow_period})"
            raise ValueError(msg)
        if signal_period < 1:
            msg = f"signal_period must be >= 1, got {signal_period}"
            raise ValueError(msg)
        self._fast_period = fast_period
        self._slow_period = slow_period
        self._signal_period = signal_period
        self._fast_mult = TWO / (Decimal(fast_period) + ONE)
        self._slow_mult = TWO / (Decimal(slow_period) + ONE)
        self._signal_mult = TWO / (Decimal(signal_period) + ONE)

        self._fast_ema = Decimal(0)
        self._slow_ema = Decimal(0)
        self._signal_ema = Decimal(0)
        self._prev_macd = Decimal(0)
        self._prev_signal = Decimal(0)
        self._candle_count = 0
        self._seeded = False

    @property
    def name(self) -> str:
        """Return the strategy name including parameters."""
        return f"macd_{self._fast_period}_{self._slow_period}_{self._signal_period}"

    def on_candle(self, candle: Candle, history: list[Candle]) -> Signal | None:
        """Evaluate the candle and return a signal on MACD/signal crossover."""
        all_count = len(history) + 1
        warmup = self._slow_period + self._signal_period
        if all_count < warmup + 1:
            self._candle_count = all_count
            return None

        close = candle.close

        if self._seeded and len(history) == self._candle_count:
            prev_macd = self._prev_macd
            prev_signal = self._prev_signal

            self._fast_ema = self._fast_ema + self._fast_mult * (close - self._fast_ema)
            self._slow_ema = self._slow_ema + self._slow_mult * (close - self._slow_ema)
            curr_macd = self._fast_ema - self._slow_ema
            self._signal_ema = self._signal_ema + self._signal_mult * (curr_macd - self._signal_ema)
            curr_signal = self._signal_ema
        else:
            closes = [c.close for c in history] + [close]
            curr_macd, curr_signal = self._full_macd_signal(closes)
            prev_macd, prev_signal = self._full_macd_signal(closes[:-1])
            self._seed_state(closes)

        self._prev_macd = curr_macd
        self._prev_signal = curr_signal
        self._candle_count = all_count
        self._seeded = True

        if prev_macd <= prev_signal and curr_macd > curr_signal:
            return Signal(
                side=Side.BUY,
                symbol=candle.symbol,
                strength=ONE,
                reason=(
                    f"MACD({self._fast_period},{self._slow_period}) "
                    f"crossed above signal({self._signal_period})"
                ),
            )
        if prev_macd >= prev_signal and curr_macd < curr_signal:
            return Signal(
                side=Side.SELL,
                symbol=candle.symbol,
                strength=ONE,
                reason=(
                    f"MACD({self._fast_period},{self._slow_period}) "
                    f"crossed below signal({self._signal_period})"
                ),
            )
        return None

    def _seed_state(self, closes: list[Decimal]) -> None:
        """Seed internal EMA state from the full close series."""
        self._fast_ema = ema_from_values(closes, self._fast_period)
        self._slow_ema = ema_from_values(closes, self._slow_period)
        macd_series = self._macd_series(closes)
        self._signal_ema = ema_from_values(macd_series, self._signal_period)

    def _full_macd_signal(self, closes: list[Decimal]) -> tuple[Decimal, Decimal]:
        """Return (MACD line, signal line) for the given price series."""
        macd_values = self._macd_series(closes)
        signal_line = ema_from_values(macd_values, self._signal_period)
        return macd_values[-1], signal_line

    def _macd_series(self, closes: list[Decimal]) -> list[Decimal]:
        """Compute MACD line values for the full close series."""
        result: list[Decimal] = []
        for i in range(self._slow_period, len(closes) + 1):
            sub = closes[:i]
            fast = ema_from_values(sub, self._fast_period)
            slow = ema_from_values(sub, self._slow_period)
            result.append(fast - slow)
        return result
