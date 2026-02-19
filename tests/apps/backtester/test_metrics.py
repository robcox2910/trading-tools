"""Tests for backtester metrics."""

from decimal import Decimal

from trading_tools.apps.backtester.metrics import (
    calculate_metrics,
    max_drawdown,
    profit_factor,
    sharpe_ratio,
    total_return,
    win_rate,
)
from trading_tools.core.models import Side, Trade

ZERO = Decimal(0)
EXPECTED_TOTAL_TRADES = 2


def _trade(entry: str, exit_: str, qty: str = "1") -> Trade:
    return Trade(
        symbol="BTC-USD",
        side=Side.BUY,
        quantity=Decimal(qty),
        entry_price=Decimal(entry),
        entry_time=1000,
        exit_price=Decimal(exit_),
        exit_time=2000,
    )


class TestTotalReturn:
    """Tests for total_return calculation."""

    def test_positive(self) -> None:
        """Test positive return calculation."""
        assert total_return(Decimal(10000), Decimal(11000)) == Decimal("0.1")

    def test_negative(self) -> None:
        """Test negative return calculation."""
        assert total_return(Decimal(10000), Decimal(9000)) == Decimal("-0.1")

    def test_zero(self) -> None:
        """Test zero return when capital is unchanged."""
        assert total_return(Decimal(10000), Decimal(10000)) == ZERO


class TestWinRate:
    """Tests for win_rate calculation."""

    def test_empty(self) -> None:
        """Test win rate with no trades."""
        assert win_rate([]) == ZERO

    def test_all_winners(self) -> None:
        """Test win rate when all trades are winners."""
        trades = [_trade("100", "110"), _trade("100", "120")]
        assert win_rate(trades) == Decimal(1)

    def test_mixed(self) -> None:
        """Test win rate with mixed winning and losing trades."""
        trades = [_trade("100", "110"), _trade("100", "90")]
        assert win_rate(trades) == Decimal("0.5")


class TestProfitFactor:
    """Tests for profit_factor calculation."""

    def test_empty(self) -> None:
        """Test profit factor with no trades."""
        assert profit_factor([]) == ZERO

    def test_no_losses(self) -> None:
        """Test profit factor with no losing trades."""
        assert profit_factor([_trade("100", "110")]) == Decimal("Infinity")

    def test_calculation(self) -> None:
        """Test profit factor calculation with wins and losses."""
        trades = [_trade("100", "120"), _trade("100", "90")]
        # profit = 20, loss = 10
        assert profit_factor(trades) == Decimal(2)


class TestMaxDrawdown:
    """Tests for max_drawdown calculation."""

    def test_empty(self) -> None:
        """Test max drawdown with no trades."""
        assert max_drawdown([], Decimal(10000)) == ZERO

    def test_no_drawdown(self) -> None:
        """Test max drawdown when equity only increases."""
        trades = [_trade("100", "110"), _trade("100", "120")]
        assert max_drawdown(trades, Decimal(10000)) == ZERO

    def test_drawdown(self) -> None:
        """Test max drawdown calculation with a loss."""
        trades = [_trade("100", "110"), _trade("100", "90"), _trade("100", "120")]
        # equity: 10000 -> 10010 (peak) -> 10000 -> 10020
        dd = max_drawdown(trades, Decimal(10000))
        expected = Decimal(10) / Decimal(10010)
        assert dd == expected


class TestSharpeRatio:
    """Tests for sharpe_ratio calculation."""

    def test_fewer_than_two(self) -> None:
        """Test sharpe ratio with fewer than two trades."""
        assert sharpe_ratio([]) == ZERO
        assert sharpe_ratio([_trade("100", "110")]) == ZERO

    def test_identical_returns(self) -> None:
        """Test sharpe ratio with identical returns gives zero."""
        trades = [_trade("100", "110"), _trade("100", "110")]
        assert sharpe_ratio(trades) == ZERO

    def test_positive_sharpe(self) -> None:
        """Test sharpe ratio is positive with increasing returns."""
        trades = [_trade("100", "110"), _trade("100", "120")]
        result = sharpe_ratio(trades)
        assert result > ZERO


class TestCalculateMetrics:
    """Tests for calculate_metrics aggregation."""

    def test_returns_all_keys(self) -> None:
        """Test that all expected metric keys are present."""
        trades = [_trade("100", "110"), _trade("100", "90")]
        metrics = calculate_metrics(trades, Decimal(10000), Decimal(10000))
        expected_keys = {
            "total_return",
            "win_rate",
            "profit_factor",
            "max_drawdown",
            "sharpe_ratio",
            "total_trades",
        }
        assert set(metrics.keys()) == expected_keys
        assert metrics["total_trades"] == Decimal(EXPECTED_TOTAL_TRADES)
