"""Bollinger Band breakout strategy."""

from decimal import Decimal

from trading_tools.core.models import Candle, Side, Signal

ONE = Decimal(1)


class BollingerStrategy:
    """Generate BUY when close crosses above upper band, SELL below lower band."""

    def __init__(self, period: int = 20, num_std: float = 2.0) -> None:
        """Initialize the Bollinger Band strategy."""
        if period < 2:  # noqa: PLR2004
            msg = f"period must be >= 2, got {period}"
            raise ValueError(msg)
        if num_std <= 0:
            msg = f"num_std must be > 0, got {num_std}"
            raise ValueError(msg)
        self._period = period
        self._num_std = Decimal(str(num_std))

    @property
    def name(self) -> str:
        """Return the strategy name including parameters."""
        return f"bollinger_{self._period}_{self._num_std}"

    def on_candle(self, candle: Candle, history: list[Candle]) -> Signal | None:
        """Evaluate the candle and return a signal on band crossover."""
        all_candles = [*history, candle]
        if len(all_candles) < self._period + 1:
            return None

        prev_close = all_candles[-2].close
        curr_close = candle.close

        _, prev_upper, prev_lower = self._bands(all_candles, offset=1)
        _, curr_upper, curr_lower = self._bands(all_candles, offset=0)

        if prev_close <= prev_upper and curr_close > curr_upper:
            return Signal(
                side=Side.BUY,
                symbol=candle.symbol,
                strength=ONE,
                reason=f"Close crossed above upper Bollinger Band({self._period}, {self._num_std})",
            )
        if prev_close >= prev_lower and curr_close < curr_lower:
            return Signal(
                side=Side.SELL,
                symbol=candle.symbol,
                strength=ONE,
                reason=f"Close crossed below lower Bollinger Band({self._period}, {self._num_std})",
            )
        return None

    def _bands(self, candles: list[Candle], offset: int) -> tuple[Decimal, Decimal, Decimal]:
        """Return (middle, upper, lower) Bollinger Bands."""
        end = len(candles) - offset
        start = end - self._period
        closes = [c.close for c in candles[start:end]]
        middle = sum(closes) / Decimal(len(closes))
        variance = sum((c - middle) ** 2 for c in closes) / Decimal(len(closes))
        std = variance.sqrt()
        upper = middle + self._num_std * std
        lower = middle - self._num_std * std
        return middle, upper, lower
