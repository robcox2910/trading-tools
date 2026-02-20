"""Moving Average Convergence Divergence strategy."""

from decimal import Decimal

from trading_tools.core.models import Candle, Side, Signal

ONE = Decimal(1)
TWO = Decimal(2)


class MacdStrategy:
    """Generate BUY when MACD crosses above signal line, SELL when below."""

    def __init__(
        self,
        fast_period: int = 12,
        slow_period: int = 26,
        signal_period: int = 9,
    ) -> None:
        """Initialize the MACD strategy."""
        if fast_period >= slow_period:
            msg = f"fast_period ({fast_period}) must be < slow_period ({slow_period})"
            raise ValueError(msg)
        if signal_period < 1:
            msg = f"signal_period must be >= 1, got {signal_period}"
            raise ValueError(msg)
        self._fast_period = fast_period
        self._slow_period = slow_period
        self._signal_period = signal_period

    @property
    def name(self) -> str:
        """Return the strategy name including parameters."""
        return f"macd_{self._fast_period}_{self._slow_period}_{self._signal_period}"

    def on_candle(self, candle: Candle, history: list[Candle]) -> Signal | None:
        """Evaluate the candle and return a signal on MACD/signal crossover."""
        all_candles = [*history, candle]
        warmup = self._slow_period + self._signal_period
        if len(all_candles) < warmup + 1:
            return None

        closes = [c.close for c in all_candles]

        curr_macd, curr_signal = self._macd_signal(closes)
        prev_macd, prev_signal = self._macd_signal(closes[:-1])

        if prev_macd <= prev_signal and curr_macd > curr_signal:
            return Signal(
                side=Side.BUY,
                symbol=candle.symbol,
                strength=ONE,
                reason=(
                    f"MACD({self._fast_period},{self._slow_period}) "
                    f"crossed above signal({self._signal_period})"
                ),
            )
        if prev_macd >= prev_signal and curr_macd < curr_signal:
            return Signal(
                side=Side.SELL,
                symbol=candle.symbol,
                strength=ONE,
                reason=(
                    f"MACD({self._fast_period},{self._slow_period}) "
                    f"crossed below signal({self._signal_period})"
                ),
            )
        return None

    def _macd_signal(self, closes: list[Decimal]) -> tuple[Decimal, Decimal]:
        """Return (MACD line, signal line) for the given price series."""
        fast_ema = self._ema(closes, self._fast_period)
        slow_ema = self._ema(closes, self._slow_period)
        macd_current = fast_ema - slow_ema

        macd_values = self._macd_series(closes)
        signal_line = self._ema_from_values(macd_values, self._signal_period)
        _ = macd_current  # current matches last of series
        return macd_values[-1], signal_line

    def _macd_series(self, closes: list[Decimal]) -> list[Decimal]:
        """Compute MACD line values for the full close series."""
        result: list[Decimal] = []
        for i in range(self._slow_period, len(closes) + 1):
            sub = closes[:i]
            fast = self._ema(sub, self._fast_period)
            slow = self._ema(sub, self._slow_period)
            result.append(fast - slow)
        return result

    @staticmethod
    def _ema(closes: list[Decimal], period: int) -> Decimal:
        """Calculate EMA seeded with SMA of the first `period` values."""
        sma = sum(closes[:period]) / Decimal(period)
        multiplier = TWO / (Decimal(period) + ONE)
        ema = sma
        for close in closes[period:]:
            ema = (close - ema) * multiplier + ema
        return ema

    @staticmethod
    def _ema_from_values(values: list[Decimal], period: int) -> Decimal:
        """Calculate EMA over an arbitrary list of Decimal values."""
        sma = sum(values[:period]) / Decimal(period)
        multiplier = TWO / (Decimal(period) + ONE)
        ema = sma
        for val in values[period:]:
            ema = (val - ema) * multiplier + ema
        return ema
