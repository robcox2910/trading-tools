"""Tests for the technical indicators module."""

from decimal import Decimal

import pytest

from trading_tools.apps.backtester.indicators import (
    adx,
    atr,
    correlation,
    ema,
    rolling_std,
    rsi,
    sma,
)
from trading_tools.core.models import Candle, Interval

_ZERO = Decimal(0)
_ONE = Decimal(1)
_HUNDRED = Decimal(100)


def _candle(
    close: str,
    *,
    high: str | None = None,
    low: str | None = None,
    ts: int = 1000,
) -> Candle:
    """Build a candle with sensible defaults for indicator testing."""
    c = Decimal(close)
    h = Decimal(high) if high is not None else c + Decimal(5)
    lo = Decimal(low) if low is not None else c - Decimal(5)
    return Candle(
        symbol="BTC-USD",
        timestamp=ts,
        open=c,
        high=h,
        low=lo,
        close=c,
        volume=Decimal(100),
        interval=Interval.H1,
    )


def _candles_from_closes(*closes: str) -> list[Candle]:
    """Build a list of candles from close price strings."""
    return [_candle(c, ts=1000 + i * 3600) for i, c in enumerate(closes)]


class TestSma:
    """Tests for the simple moving average function."""

    def test_correct_value(self) -> None:
        """Compute SMA of known values and verify the result."""
        candles = _candles_from_closes("10", "20", "30", "40", "50")
        period = 3
        expected = Decimal(40)  # (30 + 40 + 50) / 3
        assert sma(candles, period) == expected

    def test_full_series(self) -> None:
        """Compute SMA over the entire series."""
        candles = _candles_from_closes("10", "20", "30")
        assert sma(candles, 3) == Decimal(20)

    def test_insufficient_candles_raises(self) -> None:
        """Raise ValueError when fewer candles than period."""
        candles = _candles_from_closes("10", "20")
        with pytest.raises(ValueError, match="Need at least 3"):
            sma(candles, 3)

    def test_constant_prices(self) -> None:
        """Return the constant price when all closes are identical."""
        candles = _candles_from_closes("50", "50", "50", "50")
        assert sma(candles, 4) == Decimal(50)


class TestEma:
    """Tests for the exponential moving average function."""

    def test_single_period_returns_close(self) -> None:
        """EMA with period=1 equals the last close."""
        candles = _candles_from_closes("100", "200")
        result = ema(candles, 1)
        assert result == Decimal(200)

    def test_known_computation(self) -> None:
        """Verify EMA against hand-computed values."""
        candles = _candles_from_closes("10", "20", "30")
        # period=2: seed = (10+20)/2 = 15, k = 2/3
        # ema = 15 + 2/3 * (30 - 15) = 15 + 10 = 25
        result = ema(candles, 2)
        assert result == Decimal(25)

    def test_insufficient_candles_raises(self) -> None:
        """Raise ValueError when fewer candles than period."""
        candles = _candles_from_closes("10")
        with pytest.raises(ValueError, match="Need at least 5"):
            ema(candles, 5)

    def test_constant_prices(self) -> None:
        """Return the constant price when all closes are identical."""
        candles = _candles_from_closes("42", "42", "42", "42", "42")
        assert ema(candles, 3) == Decimal(42)


class TestRollingStd:
    """Tests for the rolling standard deviation function."""

    def test_known_values(self) -> None:
        """Compute std dev of known values."""
        candles = _candles_from_closes("10", "20", "30")
        # mean = 20, variance = ((100 + 0 + 100) / 3), std = sqrt(200/3)
        result = rolling_std(candles, 3)
        expected_variance = Decimal(200) / Decimal(3)
        expected = expected_variance.sqrt()
        assert abs(result - expected) < Decimal("0.0001")

    def test_constant_prices_zero_std(self) -> None:
        """Return zero when all prices are equal."""
        candles = _candles_from_closes("50", "50", "50")
        assert rolling_std(candles, 3) == _ZERO

    def test_insufficient_candles_raises(self) -> None:
        """Raise ValueError when fewer candles than period."""
        candles = _candles_from_closes("10")
        with pytest.raises(ValueError, match="Need at least 5"):
            rolling_std(candles, 5)

    def test_uses_last_n_candles(self) -> None:
        """Use only the last period candles even when more are provided."""
        candles = _candles_from_closes("999", "10", "20", "30")
        result_4 = rolling_std(candles, 3)
        candles_3 = _candles_from_closes("10", "20", "30")
        result_3 = rolling_std(candles_3, 3)
        assert result_4 == result_3


class TestAtr:
    """Tests for the Average True Range function."""

    def test_known_values(self) -> None:
        """Compute ATR on candles with known true ranges."""
        # Build candles where TR is easy to compute
        candles = [
            _candle("100", high="105", low="95"),  # anchor
            _candle("102", high="108", low="98"),  # TR = max(10, 8, 2) = 10
            _candle("104", high="110", low="100"),  # TR = max(10, 8, 2) = 10
        ]
        result = atr(candles, period=2)
        assert result == Decimal(10)

    def test_insufficient_candles_raises(self) -> None:
        """Raise ValueError when fewer candles than period + 1."""
        candles = _candles_from_closes("100")
        with pytest.raises(ValueError, match="Need at least 15"):
            atr(candles, period=14)

    def test_single_period(self) -> None:
        """ATR with period=1 equals the single true range."""
        candles = [
            _candle("100", high="100", low="100"),
            _candle("110", high="115", low="105"),  # TR = max(10, 15, 5) = 15
        ]
        assert atr(candles, period=1) == Decimal(15)


class TestRsi:
    """Tests for the Relative Strength Index function."""

    def test_all_gains_returns_100(self) -> None:
        """Return 100 when every candle is a gain (no losses)."""
        closes = [str(100 + i * 10) for i in range(16)]
        candles = _candles_from_closes(*closes)
        result = rsi(candles, period=14)
        assert result == _HUNDRED

    def test_all_losses_returns_0(self) -> None:
        """Return 0 when every candle is a loss (no gains)."""
        closes = [str(200 - i * 10) for i in range(16)]
        candles = _candles_from_closes(*closes)
        result = rsi(candles, period=14)
        assert result == _ZERO

    def test_equal_gains_losses_returns_50(self) -> None:
        """Return 50 when average gain equals average loss."""
        # Alternating up/down by 10
        closes = ["100", "110", "100", "110", "100", "110", "100", "110", "100"]
        candles = _candles_from_closes(*closes)
        result = rsi(candles, period=8)
        assert abs(result - Decimal(50)) < Decimal("0.01")

    def test_insufficient_candles_raises(self) -> None:
        """Raise ValueError when fewer candles than period + 1."""
        candles = _candles_from_closes("100", "110")
        with pytest.raises(ValueError, match="Need at least 15"):
            rsi(candles, period=14)


class TestCorrelation:
    """Tests for the Pearson correlation function."""

    def test_perfect_positive_correlation(self) -> None:
        """Return 1 when two series move identically."""
        a = _candles_from_closes("10", "20", "30", "40", "50")
        b = _candles_from_closes("100", "200", "300", "400", "500")
        result = correlation(a, b, period=5)
        assert abs(result - _ONE) < Decimal("0.0001")

    def test_perfect_negative_correlation(self) -> None:
        """Return -1 when two series move in opposite directions."""
        a = _candles_from_closes("10", "20", "30", "40", "50")
        b = _candles_from_closes("500", "400", "300", "200", "100")
        result = correlation(a, b, period=5)
        assert abs(result - Decimal(-1)) < Decimal("0.0001")

    def test_zero_variance_returns_zero(self) -> None:
        """Return 0 when one series has constant prices."""
        a = _candles_from_closes("50", "50", "50")
        b = _candles_from_closes("10", "20", "30")
        assert correlation(a, b, period=3) == _ZERO

    def test_insufficient_candles_a_raises(self) -> None:
        """Raise ValueError when series A is too short."""
        a = _candles_from_closes("10")
        b = _candles_from_closes("10", "20", "30")
        with pytest.raises(ValueError, match="series A"):
            correlation(a, b, period=3)

    def test_insufficient_candles_b_raises(self) -> None:
        """Raise ValueError when series B is too short."""
        a = _candles_from_closes("10", "20", "30")
        b = _candles_from_closes("10")
        with pytest.raises(ValueError, match="series B"):
            correlation(a, b, period=3)


class TestAdx:
    """Tests for the Average Directional Index function."""

    def test_strong_trend_high_adx(self) -> None:
        """Verify that a strong trending series produces a high ADX."""
        # Steadily increasing with clear directional movement
        candles = [
            Candle(
                symbol="BTC-USD",
                timestamp=1000 + i * 3600,
                open=Decimal(str(100 + i * 5)),
                high=Decimal(str(100 + i * 5 + 3)),
                low=Decimal(str(100 + i * 5 - 1)),
                close=Decimal(str(100 + i * 5)),
                volume=Decimal(100),
                interval=Interval.H1,
            )
            for i in range(30)
        ]
        result = adx(candles, period=14)
        min_trending_adx = Decimal(25)
        assert result > min_trending_adx

    def test_insufficient_candles_raises(self) -> None:
        """Raise ValueError when fewer candles than 2*period+1."""
        candles = _candles_from_closes(*[str(i) for i in range(10)])
        with pytest.raises(ValueError, match="Need at least 29"):
            adx(candles, period=14)

    def test_returns_between_0_and_100(self) -> None:
        """Verify ADX is bounded between 0 and 100."""
        candles = [
            Candle(
                symbol="BTC-USD",
                timestamp=1000 + i * 3600,
                open=Decimal(str(100 + i)),
                high=Decimal(str(105 + i)),
                low=Decimal(str(95 + i)),
                close=Decimal(str(100 + i)),
                volume=Decimal(100),
                interval=Interval.H1,
            )
            for i in range(30)
        ]
        result = adx(candles, period=5)
        assert _ZERO <= result <= _HUNDRED
