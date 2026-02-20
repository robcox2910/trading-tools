"""Relative Strength Index (RSI) mean-reversion strategy.

How it works:
    RSI measures how much of a stock's recent price movement has been
    upward vs downward. It produces a number between 0 and 100:

    - RSI near 100 = almost all recent moves were UP (price may be
      "overbought" -- it went up too fast and might come back down).
    - RSI near 0 = almost all recent moves were DOWN (price may be
      "oversold" -- it dropped too fast and might bounce back up).

    The calculation uses "Wilder's smoothing":
      1. Look at each candle-to-candle price change over the last N candles.
      2. Separate the changes into gains (went up) and losses (went down).
      3. Calculate the average gain and average loss.
      4. RS = average_gain / average_loss
      5. RSI = 100 - 100 / (1 + RS)

    When RSI crosses below the oversold threshold (e.g. 30), it signals a
    BUY (the asset has dropped a lot and may bounce). When RSI crosses above
    the overbought threshold (e.g. 70), it signals a SELL (the asset has
    risen a lot and may pull back).

What it tries to achieve:
    Profit from price "snapback" -- the tendency for prices that moved too
    far in one direction to reverse. This is the opposite of trend-following;
    instead of riding a trend, it bets the trend has gone too far and will
    correct.

Performance note:
    Uses incremental Wilder's smoothing internally. After the warm-up
    period, each candle requires only one addition and one division per
    average (O(1) per candle).

Params:
    period:     Lookback window for RSI calculation (default 14).
    overbought: RSI level above which a SELL signal fires (default 70).
    oversold:   RSI level below which a BUY signal fires (default 30).
"""

from decimal import Decimal

from trading_tools.core.models import Candle, Side, Signal

ZERO = Decimal(0)
ONE = Decimal(1)
HUNDRED = Decimal(100)


class RsiStrategy:
    """Generate BUY when RSI drops below oversold, SELL when above overbought.

    RSI is a "contrarian" indicator. When everyone is selling (RSI low),
    this strategy buys because it expects the price to bounce back up.
    When everyone is buying (RSI high), it sells because it expects the
    price to come back down.
    """

    def __init__(
        self,
        period: int = 14,
        overbought: int = 70,
        oversold: int = 30,
    ) -> None:
        """Initialize the RSI strategy."""
        if period < 2:  # noqa: PLR2004
            msg = f"period must be >= 2, got {period}"
            raise ValueError(msg)
        if not (0 < oversold < overbought < 100):  # noqa: PLR2004
            msg = f"Need 0 < oversold ({oversold}) < overbought ({overbought}) < 100"
            raise ValueError(msg)
        self._period = period
        self._dec_period = Decimal(period)
        self._overbought = Decimal(overbought)
        self._oversold = Decimal(oversold)

        self._avg_gain = Decimal(0)
        self._avg_loss = Decimal(0)
        self._prev_rsi = Decimal(0)
        self._prev_close = Decimal(0)
        self._candle_count = 0
        self._seeded = False

    @property
    def name(self) -> str:
        """Return the strategy name including parameters."""
        return f"rsi_{self._period}_{self._oversold}_{self._overbought}"

    def on_candle(self, candle: Candle, history: list[Candle]) -> Signal | None:
        """Evaluate the candle and return a signal based on RSI thresholds."""
        all_count = len(history) + 1
        needed = self._period + 1
        if all_count < needed + 1:
            self._candle_count = all_count
            return None

        if self._seeded and len(history) == self._candle_count:
            prev_rsi = self._prev_rsi
            delta = candle.close - self._prev_close
            gain = max(delta, ZERO)
            loss = max(-delta, ZERO)
            self._avg_gain = (self._avg_gain * (self._dec_period - ONE) + gain) / self._dec_period
            self._avg_loss = (self._avg_loss * (self._dec_period - ONE) + loss) / self._dec_period
            curr_rsi = self._rsi_from_avgs(self._avg_gain, self._avg_loss)
        else:
            all_candles = [*history, candle]
            prev_rsi = self._compute_rsi(all_candles[: len(all_candles) - 1])
            curr_rsi = self._compute_rsi(all_candles)
            self._avg_gain, self._avg_loss = self._compute_avgs(all_candles)

        self._prev_rsi = curr_rsi
        self._prev_close = candle.close
        self._candle_count = all_count
        self._seeded = True

        if prev_rsi >= self._oversold and curr_rsi < self._oversold:
            return Signal(
                side=Side.BUY,
                symbol=candle.symbol,
                strength=ONE,
                reason=f"RSI({self._period}) crossed below {self._oversold}",
            )
        if prev_rsi <= self._overbought and curr_rsi > self._overbought:
            return Signal(
                side=Side.SELL,
                symbol=candle.symbol,
                strength=ONE,
                reason=f"RSI({self._period}) crossed above {self._overbought}",
            )
        return None

    def _compute_rsi(self, candles: list[Candle]) -> Decimal:
        """Calculate RSI using Wilder's smoothing method."""
        avg_gain, avg_loss = self._compute_avgs(candles)
        return self._rsi_from_avgs(avg_gain, avg_loss)

    def _compute_avgs(self, candles: list[Candle]) -> tuple[Decimal, Decimal]:
        """Compute average gain and average loss for RSI calculation."""
        closes = [c.close for c in candles]
        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]

        period = self._period
        gains = [max(d, ZERO) for d in deltas[:period]]
        losses = [max(-d, ZERO) for d in deltas[:period]]

        avg_gain = sum(gains) / self._dec_period
        avg_loss = sum(losses) / self._dec_period

        for delta in deltas[period:]:
            gain = max(delta, ZERO)
            loss = max(-delta, ZERO)
            avg_gain = (avg_gain * (self._dec_period - ONE) + gain) / self._dec_period
            avg_loss = (avg_loss * (self._dec_period - ONE) + loss) / self._dec_period

        return avg_gain, avg_loss

    @staticmethod
    def _rsi_from_avgs(avg_gain: Decimal, avg_loss: Decimal) -> Decimal:
        """Compute RSI value from average gain and loss."""
        if avg_loss == ZERO:
            return HUNDRED
        rs = avg_gain / avg_loss
        return HUNDRED - HUNDRED / (ONE + rs)
