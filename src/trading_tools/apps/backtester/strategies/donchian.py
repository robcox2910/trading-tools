"""Donchian Channel breakout strategy."""

from collections import deque
from decimal import Decimal

from trading_tools.core.models import Candle, Side, Signal

ONE = Decimal(1)


class DonchianStrategy:
    """Generate BUY on upper channel breakout, SELL on lower channel breakout."""

    def __init__(self, period: int = 20) -> None:
        """Initialize the Donchian Channel strategy."""
        if period < 1:
            msg = f"period must be >= 1, got {period}"
            raise ValueError(msg)
        self._period = period
        self._highs: deque[Decimal] = deque(maxlen=period)
        self._lows: deque[Decimal] = deque(maxlen=period)
        self._candle_count = 0

    @property
    def name(self) -> str:
        """Return the strategy name including parameters."""
        return f"donchian_{self._period}"

    def on_candle(self, candle: Candle, history: list[Candle]) -> Signal | None:  # noqa: ARG002
        """Evaluate the candle and return a signal on channel breakout."""
        self._candle_count += 1

        if self._candle_count <= self._period:
            self._highs.append(candle.high)
            self._lows.append(candle.low)
            return None

        upper = max(self._highs)
        lower = min(self._lows)

        self._highs.append(candle.high)
        self._lows.append(candle.low)

        if candle.close > upper:
            return Signal(
                side=Side.BUY,
                symbol=candle.symbol,
                strength=ONE,
                reason=f"Close broke above Donchian({self._period}) upper channel",
            )
        if candle.close < lower:
            return Signal(
                side=Side.SELL,
                symbol=candle.symbol,
                strength=ONE,
                reason=f"Close broke below Donchian({self._period}) lower channel",
            )
        return None
