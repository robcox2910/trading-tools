"""Volume Weighted Average Price strategy."""

from collections import deque
from decimal import Decimal

from trading_tools.core.models import Candle, Side, Signal

ONE = Decimal(1)


class VwapStrategy:
    """Generate BUY when price crosses below VWAP, SELL when above."""

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
                self._prev_vwap = self._compute_vwap()
            return None

        curr_vwap = self._compute_vwap()
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

    def _compute_vwap(self) -> Decimal:
        """Compute VWAP from the current window."""
        total_volume = sum(self._volumes)
        if total_volume == 0:
            return Decimal(0)
        return sum(self._close_volume) / total_volume
