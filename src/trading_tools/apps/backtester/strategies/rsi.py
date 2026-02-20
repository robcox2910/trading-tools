"""Relative Strength Index mean-reversion strategy."""

from decimal import Decimal

from trading_tools.core.models import Candle, Side, Signal

ZERO = Decimal(0)
ONE = Decimal(1)


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
        self._overbought = Decimal(overbought)
        self._oversold = Decimal(oversold)

    @property
    def name(self) -> str:
        """Return the strategy name including parameters."""
        return f"rsi_{self._period}_{self._oversold}_{self._overbought}"

    def on_candle(self, candle: Candle, history: list[Candle]) -> Signal | None:
        """Evaluate the candle and return a signal based on RSI thresholds."""
        all_candles = [*history, candle]
        needed = self._period + 1
        if len(all_candles) < needed + 1:
            return None

        prev_rsi = self._rsi(all_candles[: len(all_candles) - 1])
        curr_rsi = self._rsi(all_candles)

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

    def _rsi(self, candles: list[Candle]) -> Decimal:
        """Calculate RSI using Wilder's smoothing method."""
        closes = [c.close for c in candles]
        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]

        period = self._period
        gains = [max(d, ZERO) for d in deltas[:period]]
        losses = [max(-d, ZERO) for d in deltas[:period]]

        avg_gain = sum(gains) / Decimal(period)
        avg_loss = sum(losses) / Decimal(period)

        dec_period = Decimal(period)
        for delta in deltas[period:]:
            gain = max(delta, ZERO)
            loss = max(-delta, ZERO)
            avg_gain = (avg_gain * (dec_period - ONE) + gain) / dec_period
            avg_loss = (avg_loss * (dec_period - ONE) + loss) / dec_period

        if avg_loss == ZERO:
            return Decimal(100)
        rs = avg_gain / avg_loss
        return Decimal(100) - Decimal(100) / (ONE + rs)
