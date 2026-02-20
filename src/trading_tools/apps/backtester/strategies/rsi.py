"""Relative Strength Index mean-reversion strategy."""

from decimal import Decimal

from trading_tools.core.models import Candle, Side, Signal

ZERO = Decimal(0)
ONE = Decimal(1)
HUNDRED = Decimal(100)


class RsiStrategy:
    """Generate BUY when RSI drops below oversold, SELL when above overbought."""

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
