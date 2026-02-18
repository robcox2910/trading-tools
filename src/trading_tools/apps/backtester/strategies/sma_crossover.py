"""Simple Moving Average crossover strategy."""

from decimal import Decimal

from trading_tools.core.models import Candle, Side, Signal


class SmaCrossoverStrategy:
    """Generates BUY when short SMA crosses above long SMA, SELL when below."""

    def __init__(self, short_period: int = 10, long_period: int = 20) -> None:
        if short_period >= long_period:
            msg = f"short_period ({short_period}) must be < long_period ({long_period})"
            raise ValueError(msg)
        self._short_period = short_period
        self._long_period = long_period

    @property
    def name(self) -> str:
        return f"sma_crossover_{self._short_period}_{self._long_period}"

    def on_candle(self, candle: Candle, history: list[Candle]) -> Signal | None:
        all_candles = [*history, candle]
        if len(all_candles) < self._long_period + 1:
            return None

        current_short = self._sma(all_candles, self._short_period, 0)
        current_long = self._sma(all_candles, self._long_period, 0)
        prev_short = self._sma(all_candles, self._short_period, 1)
        prev_long = self._sma(all_candles, self._long_period, 1)

        if prev_short <= prev_long and current_short > current_long:
            return Signal(
                side=Side.BUY,
                symbol=candle.symbol,
                strength=Decimal("1"),
                reason=f"SMA{self._short_period} crossed above SMA{self._long_period}",
            )
        if prev_short >= prev_long and current_short < current_long:
            return Signal(
                side=Side.SELL,
                symbol=candle.symbol,
                strength=Decimal("1"),
                reason=f"SMA{self._short_period} crossed below SMA{self._long_period}",
            )
        return None

    @staticmethod
    def _sma(candles: list[Candle], period: int, offset: int) -> Decimal:
        """Calculate SMA of closing prices.

        offset=0 means ending at the last candle, offset=1 means ending one before last.
        """
        end = len(candles) - offset
        start = end - period
        closes = [c.close for c in candles[start:end]]
        return sum(closes) / Decimal(len(closes))
