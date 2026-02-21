"""Exponential Moving Average (EMA) crossover strategy.

How it works:
    An EMA is like an SMA (a running average of prices), but it gives more
    weight to the most recent prices. Imagine you're grading homework and
    the latest assignments count more than the ones from weeks ago -- that's
    how an EMA treats price data. The formula is:

        new_ema = previous_ema + multiplier * (new_price - previous_ema)

    where multiplier = 2 / (period + 1). A smaller period makes the EMA
    react faster to price changes.

    This strategy watches two EMAs: a short (fast) one and a long (slow)
    one. When the fast EMA crosses above the slow EMA it means recent
    prices are climbing faster than the longer-term trend -- BUY signal.
    When the fast EMA crosses below -- SELL signal.

What it tries to achieve:
    Catch trend reversals earlier than the SMA crossover strategy. Because
    the EMA puts more emphasis on recent prices, crossovers happen sooner,
    which means you enter trades earlier. The downside is more false signals
    in choppy (sideways) markets.

Performance note:
    This strategy caches its EMA values internally. After the first candle,
    each subsequent candle only needs one multiplication and one addition
    per EMA (O(1) per candle) instead of recalculating from the entire
    history each time.

Params:
    short_period: Number of candles for the fast EMA (default 10).
    long_period:  Number of candles for the slow EMA (default 20).
"""

from decimal import Decimal

from trading_tools.core.models import ONE, TWO, Candle, Side, Signal


class EmaCrossoverStrategy:
    """Generate BUY when the short EMA crosses above the long EMA, SELL when below.

    Like the SMA crossover but more sensitive to recent price action. The
    EMA "forgets" old prices gradually rather than dropping them all at once,
    which produces smoother crossover signals.
    """

    def __init__(self, short_period: int = 10, long_period: int = 20) -> None:
        """Initialize the EMA crossover strategy."""
        if short_period >= long_period:
            msg = f"short_period ({short_period}) must be < long_period ({long_period})"
            raise ValueError(msg)
        self._short_period = short_period
        self._long_period = long_period
        self._short_mult = TWO / (Decimal(short_period) + ONE)
        self._long_mult = TWO / (Decimal(long_period) + ONE)

        self._short_ema = Decimal(0)
        self._long_ema = Decimal(0)
        self._candle_count = 0
        self._seeded = False

    @property
    def name(self) -> str:
        """Return the strategy name including period parameters."""
        return f"ema_crossover_{self._short_period}_{self._long_period}"

    def on_candle(self, candle: Candle, history: list[Candle]) -> Signal | None:
        """Evaluate the candle and return a signal if EMA lines cross."""
        all_count = len(history) + 1
        if all_count < self._long_period + 1:
            self._candle_count = all_count
            return None

        close = candle.close

        if self._seeded and len(history) == self._candle_count:
            prev_short = self._short_ema
            prev_long = self._long_ema
            curr_short = prev_short + self._short_mult * (close - prev_short)
            curr_long = prev_long + self._long_mult * (close - prev_long)
        else:
            closes = [c.close for c in history] + [close]
            curr_short = self._ema(closes, self._short_period)
            curr_long = self._ema(closes, self._long_period)
            prev_short = self._ema(closes[:-1], self._short_period)
            prev_long = self._ema(closes[:-1], self._long_period)

        self._short_ema = curr_short
        self._long_ema = curr_long
        self._candle_count = all_count
        self._seeded = True

        if prev_short <= prev_long and curr_short > curr_long:
            return Signal(
                side=Side.BUY,
                symbol=candle.symbol,
                strength=ONE,
                reason=f"EMA{self._short_period} crossed above EMA{self._long_period}",
            )
        if prev_short >= prev_long and curr_short < curr_long:
            return Signal(
                side=Side.SELL,
                symbol=candle.symbol,
                strength=ONE,
                reason=f"EMA{self._short_period} crossed below EMA{self._long_period}",
            )
        return None

    @staticmethod
    def _ema(closes: list[Decimal], period: int) -> Decimal:
        """Calculate EMA seeded with SMA of the first `period` values."""
        sma = sum(closes[:period]) / Decimal(period)
        multiplier = TWO / (Decimal(period) + ONE)
        ema = sma
        for close in closes[period:]:
            ema = (close - ema) * multiplier + ema
        return ema
