"""Technical indicator functions for the backtester.

Provide pure functions that compute common technical indicators from
sequences of ``Candle`` objects. All functions use ``Decimal`` arithmetic
for exact calculations and raise ``ValueError`` when given insufficient
data. These indicators are shared across strategies, the regime detector,
and portfolio modules that need volatility or correlation measurements.
"""

from collections.abc import Sequence
from decimal import Decimal

from trading_tools.core.models import HUNDRED, ONE, TWO, ZERO, Candle


def sma(candles: Sequence[Candle], period: int) -> Decimal:
    """Compute the simple moving average of close prices over the last ``period`` candles.

    Args:
        candles: Sequence of candles (at least ``period`` items required).
        period: Number of candles to average over.

    Returns:
        The arithmetic mean of the last ``period`` close prices.

    Raises:
        ValueError: If fewer than ``period`` candles are provided.

    """
    if len(candles) < period:
        msg = f"Need at least {period} candles for SMA, got {len(candles)}"
        raise ValueError(msg)
    closes = [c.close for c in candles[-period:]]
    return sum(closes) / Decimal(period)


def ema_from_values(values: Sequence[Decimal], period: int) -> Decimal:
    """Compute the exponential moving average over a sequence of Decimal values.

    Seed the EMA with the SMA of the first ``period`` values, then apply
    the standard EMA formula: ``ema = prev + k * (value - prev)``
    where ``k = 2 / (period + 1)``.

    Args:
        values: Sequence of Decimal values (at least ``period`` items required).
        period: Lookback window for the EMA.

    Returns:
        The current EMA value.

    Raises:
        ValueError: If fewer than ``period`` values are provided.

    """
    if len(values) < period:
        msg = f"Need at least {period} values for EMA, got {len(values)}"
        raise ValueError(msg)
    seed = sum(values[:period]) / Decimal(period)
    multiplier = TWO / (Decimal(period) + ONE)
    result = seed
    for val in values[period:]:
        result = (val - result) * multiplier + result
    return result


def ema(candles: Sequence[Candle], period: int) -> Decimal:
    """Compute the exponential moving average of close prices.

    Seed the EMA with the SMA of the first ``period`` values, then apply
    the standard EMA formula for remaining values: ``ema = prev + k * (close - prev)``
    where ``k = 2 / (period + 1)``.

    Args:
        candles: Sequence of candles (at least ``period`` items required).
        period: Lookback window for the EMA.

    Returns:
        The current EMA value.

    Raises:
        ValueError: If fewer than ``period`` candles are provided.

    """
    if len(candles) < period:
        msg = f"Need at least {period} candles for EMA, got {len(candles)}"
        raise ValueError(msg)
    closes = [c.close for c in candles]
    return ema_from_values(closes, period)


def rolling_std(candles: Sequence[Candle], period: int = 20) -> Decimal:
    """Compute the rolling standard deviation of close prices over ``period`` candles.

    Use population standard deviation (divide by N, not N-1) consistent
    with how Bollinger Bands and z-score calculations work in this codebase.

    Args:
        candles: Sequence of candles (at least ``period`` items required).
        period: Number of candles to compute the standard deviation over.

    Returns:
        The population standard deviation of the last ``period`` close prices.

    Raises:
        ValueError: If fewer than ``period`` candles are provided.

    """
    if len(candles) < period:
        msg = f"Need at least {period} candles for rolling_std, got {len(candles)}"
        raise ValueError(msg)
    closes = [c.close for c in candles[-period:]]
    mean = sum(closes) / Decimal(period)
    variance = sum((c - mean) ** 2 for c in closes) / Decimal(period)
    return variance.sqrt()


def atr(candles: Sequence[Candle], period: int = 14) -> Decimal:
    """Compute the Average True Range over the last ``period`` candles.

    True Range for each candle is the maximum of:
    - ``high - low``
    - ``|high - prev_close|``
    - ``|low - prev_close|``

    The ATR is the simple average of the last ``period`` true ranges.
    This requires ``period + 1`` candles (one extra for the first
    previous close).

    Args:
        candles: Sequence of candles (at least ``period + 1`` items required).
        period: Number of true range values to average.

    Returns:
        The ATR value.

    Raises:
        ValueError: If fewer than ``period + 1`` candles are provided.

    """
    needed = period + 1
    if len(candles) < needed:
        msg = f"Need at least {needed} candles for ATR({period}), got {len(candles)}"
        raise ValueError(msg)
    recent = list(candles[-needed:])
    true_ranges: list[Decimal] = []
    for i in range(1, len(recent)):
        high = recent[i].high
        low = recent[i].low
        prev_close = recent[i - 1].close
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)
    return sum(true_ranges) / Decimal(period)


def rsi(candles: Sequence[Candle], period: int = 14) -> Decimal:
    """Compute the Relative Strength Index using Wilder's smoothing.

    Require at least ``period + 1`` candles to produce a meaningful RSI
    value. Use Wilder's smoothing (exponential with decay = 1/period)
    for the average gain and average loss series.

    Args:
        candles: Sequence of candles (at least ``period + 1`` items required).
        period: Lookback window for the RSI calculation.

    Returns:
        The RSI value between 0 and 100.

    Raises:
        ValueError: If fewer than ``period + 1`` candles are provided.

    """
    needed = period + 1
    if len(candles) < needed:
        msg = f"Need at least {needed} candles for RSI({period}), got {len(candles)}"
        raise ValueError(msg)
    closes = [c.close for c in candles]
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]

    dec_period = Decimal(period)
    gains = [max(d, ZERO) for d in deltas[:period]]
    losses = [max(-d, ZERO) for d in deltas[:period]]

    avg_gain = sum(gains) / dec_period
    avg_loss = sum(losses) / dec_period

    for delta in deltas[period:]:
        gain = max(delta, ZERO)
        loss = max(-delta, ZERO)
        avg_gain = (avg_gain * (dec_period - ONE) + gain) / dec_period
        avg_loss = (avg_loss * (dec_period - ONE) + loss) / dec_period

    if avg_loss == ZERO:
        return HUNDRED
    rs = avg_gain / avg_loss
    return HUNDRED - HUNDRED / (ONE + rs)


def correlation(
    candles_a: Sequence[Candle],
    candles_b: Sequence[Candle],
    period: int = 20,
) -> Decimal:
    """Compute the Pearson correlation coefficient of close prices between two series.

    Use the last ``period`` candles from each series. Return a value
    between -1 and 1. Return zero when either series has zero variance
    (constant prices).

    Args:
        candles_a: First candle series (at least ``period`` items).
        candles_b: Second candle series (at least ``period`` items).
        period: Number of candles to correlate over.

    Returns:
        The Pearson correlation coefficient.

    Raises:
        ValueError: If either series has fewer than ``period`` candles.

    """
    if len(candles_a) < period:
        msg = f"Need at least {period} candles for correlation, series A has {len(candles_a)}"
        raise ValueError(msg)
    if len(candles_b) < period:
        msg = f"Need at least {period} candles for correlation, series B has {len(candles_b)}"
        raise ValueError(msg)

    a_closes = [c.close for c in candles_a[-period:]]
    b_closes = [c.close for c in candles_b[-period:]]

    dec_n = Decimal(period)
    mean_a = sum(a_closes) / dec_n
    mean_b = sum(b_closes) / dec_n

    cov = sum((a - mean_a) * (b - mean_b) for a, b in zip(a_closes, b_closes, strict=True)) / dec_n
    var_a = sum((a - mean_a) ** 2 for a in a_closes) / dec_n
    var_b = sum((b - mean_b) ** 2 for b in b_closes) / dec_n

    if ZERO in (var_a, var_b):
        return ZERO

    return cov / (var_a.sqrt() * var_b.sqrt())


def adx(candles: Sequence[Candle], period: int = 14) -> Decimal:
    """Compute the Average Directional Index (ADX).

    The ADX measures trend strength regardless of direction. It is computed
    from the smoothed directional movement indicators (+DI and -DI) and
    the true range. Values above 25 typically indicate a trending market,
    while values below 25 suggest a range-bound or choppy market.

    Require at least ``2 * period + 1`` candles: ``period + 1`` for the
    initial DI calculations, then ``period`` more for the ADX smoothing.

    Args:
        candles: Sequence of candles (at least ``2 * period + 1`` items).
        period: Lookback period for both DI and ADX smoothing.

    Returns:
        The ADX value between 0 and 100.

    Raises:
        ValueError: If fewer than ``2 * period + 1`` candles are provided.

    """
    needed = 2 * period + 1
    if len(candles) < needed:
        msg = f"Need at least {needed} candles for ADX({period}), got {len(candles)}"
        raise ValueError(msg)

    candle_list = list(candles)
    dec_period = Decimal(period)

    # Compute true range and directional movement for each bar
    tr_list: list[Decimal] = []
    plus_dm_list: list[Decimal] = []
    minus_dm_list: list[Decimal] = []

    for i in range(1, len(candle_list)):
        high = candle_list[i].high
        low = candle_list[i].low
        prev_high = candle_list[i - 1].high
        prev_low = candle_list[i - 1].low
        prev_close = candle_list[i - 1].close

        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        tr_list.append(tr)

        up_move = high - prev_high
        down_move = prev_low - low

        plus_dm = up_move if (up_move > down_move and up_move > ZERO) else ZERO
        minus_dm = down_move if (down_move > up_move and down_move > ZERO) else ZERO
        plus_dm_list.append(plus_dm)
        minus_dm_list.append(minus_dm)

    # Wilder's smoothing for initial period sums
    smoothed_tr = sum(tr_list[:period], ZERO)
    smoothed_plus_dm = sum(plus_dm_list[:period], ZERO)
    smoothed_minus_dm = sum(minus_dm_list[:period], ZERO)

    # Compute DX values starting from the first complete period
    dx_values: list[Decimal] = []

    def _compute_dx(s_tr: Decimal, s_plus: Decimal, s_minus: Decimal) -> Decimal:
        plus_di = (s_plus / s_tr * HUNDRED) if s_tr != ZERO else ZERO
        minus_di = (s_minus / s_tr * HUNDRED) if s_tr != ZERO else ZERO
        di_sum = plus_di + minus_di
        if di_sum == ZERO:
            return ZERO
        return abs(plus_di - minus_di) / di_sum * HUNDRED

    dx_values.append(_compute_dx(smoothed_tr, smoothed_plus_dm, smoothed_minus_dm))

    # Continue smoothing and collecting DX values
    for i in range(period, len(tr_list)):
        smoothed_tr = smoothed_tr - smoothed_tr / dec_period + tr_list[i]
        smoothed_plus_dm = smoothed_plus_dm - smoothed_plus_dm / dec_period + plus_dm_list[i]
        smoothed_minus_dm = smoothed_minus_dm - smoothed_minus_dm / dec_period + minus_dm_list[i]
        dx_values.append(_compute_dx(smoothed_tr, smoothed_plus_dm, smoothed_minus_dm))

    # ADX = Wilder's smoothed average of DX values
    # First ADX = average of first `period` DX values
    if len(dx_values) < period:
        return sum(dx_values) / Decimal(len(dx_values))

    adx_val = sum(dx_values[:period]) / dec_period
    for dx in dx_values[period:]:
        adx_val = (adx_val * (dec_period - ONE) + dx) / dec_period

    return adx_val
