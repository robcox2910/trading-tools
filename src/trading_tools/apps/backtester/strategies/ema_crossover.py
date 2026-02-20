"""Exponential Moving Average crossover strategy."""

from decimal import Decimal

from trading_tools.core.models import Candle, Side, Signal

ONE = Decimal(1)
TWO = Decimal(2)


class EmaCrossoverStrategy:
    """Generate BUY when short EMA crosses above long EMA, SELL when below."""

    def __init__(self, short_period: int = 10, long_period: int = 20) -> None:
        """Initialize the EMA crossover strategy."""
        if short_period >= long_period:
            msg = f"short_period ({short_period}) must be < long_period ({long_period})"
            raise ValueError(msg)
        self._short_period = short_period
        self._long_period = long_period

    @property
    def name(self) -> str:
        """Return the strategy name including period parameters."""
        return f"ema_crossover_{self._short_period}_{self._long_period}"

    def on_candle(self, candle: Candle, history: list[Candle]) -> Signal | None:
        """Evaluate the candle and return a signal if EMA lines cross."""
        all_candles = [*history, candle]
        if len(all_candles) < self._long_period + 1:
            return None

        closes = [c.close for c in all_candles]

        curr_short = self._ema(closes, self._short_period)
        curr_long = self._ema(closes, self._long_period)
        prev_short = self._ema(closes[:-1], self._short_period)
        prev_long = self._ema(closes[:-1], self._long_period)

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
