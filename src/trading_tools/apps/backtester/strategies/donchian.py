"""Donchian Channel breakout strategy (Turtle Trading).

How it works:
    The Donchian Channel draws a box around the price using two simple
    lines:

    - Upper channel = the highest high of the last N candles.
    - Lower channel = the lowest low of the last N candles.

    If the last 20 candles had a highest high of $110 and a lowest low of
    $90, the channel is $90-$110. The price bounces around inside this box
    most of the time.

    The strategy generates:
    - BUY when the price closes above the upper channel. This is called a
      "breakout" -- the price just hit a new high for the lookback period,
      suggesting a strong upward move is starting.
    - SELL when the price closes below the lower channel. This is a
      "breakdown" -- the price just hit a new low, suggesting a strong
      downward move is starting.

    This is the basis of the famous "Turtle Trading" system from the 1980s,
    where a group of novice traders were taught this simple rule and made
    millions in the futures markets.

What it tries to achieve:
    Ride big trends by entering at the exact moment the price breaks out
    of its recent range. The idea is that when a market makes a new high
    (or low), it often continues in that direction for a while. You'll
    have many small losing trades (false breakouts) but the few big winners
    should more than pay for the losses.

Params:
    period: Number of candles to look back for the channel (default 20).
"""

from collections import deque
from decimal import Decimal

from trading_tools.core.models import Candle, Side, Signal

ONE = Decimal(1)


class DonchianStrategy:
    """Generate BUY on upper channel breakout, SELL on lower channel breakout.

    Picture a box drawn around the last N candles' highs and lows. The
    price usually stays inside the box. This strategy acts only when the
    price punches through the top or bottom of the box, betting that the
    breakout will continue.
    """

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
