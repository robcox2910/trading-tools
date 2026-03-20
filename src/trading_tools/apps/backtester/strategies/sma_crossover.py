"""Simple Moving Average (SMA) crossover strategy.

How it works:
    An SMA is the plain average of the last N closing prices. For example,
    a 10-day SMA adds up the last 10 closes and divides by 10. Every new
    candle, the oldest price drops off and the newest one is added.

    This strategy uses *two* SMAs with different lookback periods -- a
    "short" one (e.g. 10) that reacts quickly and a "long" one (e.g. 20)
    that moves slowly. When the fast line crosses above the slow line it
    means recent prices are rising faster than the longer-term trend, so
    the strategy emits a BUY signal. When the fast line drops below the
    slow line, it means the short-term trend has turned downward, so it
    emits a SELL signal.

What it tries to achieve:
    Capture medium-term trend changes. By waiting for a crossover instead
    of reacting to every price tick, the strategy filters out random noise
    and only trades when the direction of the market genuinely shifts.
    The trade-off is that signals arrive with a delay -- the trend has to
    establish itself before the averages cross.

Params:
    short_period: Number of candles for the fast-moving average (default 10).
    long_period:  Number of candles for the slow-moving average (default 20).
"""

from trading_tools.apps.backtester.indicators import detect_crossover
from trading_tools.apps.backtester.indicators import sma as compute_sma
from trading_tools.core.models import ONE, Candle, Side, Signal


class SmaCrossoverStrategy:
    """Generate BUY when the short SMA crosses above the long SMA, SELL when below.

    Think of the short SMA as a "what's happening now" line and the long SMA
    as a "what's been happening overall" line. When "now" overtakes "overall",
    the market is gaining momentum upward, and vice versa.
    """

    def __init__(self, short_period: int = 10, long_period: int = 20) -> None:
        """Initialize the SMA crossover strategy."""
        if short_period >= long_period:
            msg = f"short_period ({short_period}) must be < long_period ({long_period})"
            raise ValueError(msg)
        self._short_period = short_period
        self._long_period = long_period

    @property
    def name(self) -> str:
        """Return the strategy name including period parameters."""
        return f"sma_crossover_{self._short_period}_{self._long_period}"

    def on_candle(self, candle: Candle, history: list[Candle]) -> Signal | None:
        """Evaluate the candle and return a signal if SMA lines cross."""
        all_candles = [*history, candle]
        if len(all_candles) < self._long_period + 1:
            return None

        current_short = compute_sma(all_candles, self._short_period)
        current_long = compute_sma(all_candles, self._long_period)
        prev_short = compute_sma(all_candles[:-1], self._short_period)
        prev_long = compute_sma(all_candles[:-1], self._long_period)

        cross = detect_crossover(prev_short, current_short, prev_long, current_long)
        if cross == 1:
            return Signal(
                side=Side.BUY,
                symbol=candle.symbol,
                strength=ONE,
                reason=f"SMA{self._short_period} crossed above SMA{self._long_period}",
            )
        if cross == -1:
            return Signal(
                side=Side.SELL,
                symbol=candle.symbol,
                strength=ONE,
                reason=f"SMA{self._short_period} crossed below SMA{self._long_period}",
            )
        return None
