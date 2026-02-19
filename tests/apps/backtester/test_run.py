"""Tests for the backtester CLI."""

from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from trading_tools.apps.backtester.run import app
from trading_tools.core.models import Candle, Interval

CSV_HEADER = "symbol,timestamp,open,high,low,close,volume,interval\n"
CSV_ROW_1 = "BTC-USD,1000,100,110,90,105,50,1h\n"
CSV_ROW_2 = "BTC-USD,2000,105,115,95,110,60,1h\n"

_EXPECTED_CANDLE_COUNT = 2

runner = CliRunner()


@pytest.fixture
def csv_file(tmp_path: Path) -> Path:
    """Create a temporary CSV file with candle data."""
    f = tmp_path / "candles.csv"
    f.write_text(CSV_HEADER + CSV_ROW_1 + CSV_ROW_2)
    return f


_SAMPLE_CANDLES = [
    Candle(
        symbol="BTC-USD",
        timestamp=1000,
        open=Decimal(100),
        high=Decimal(110),
        low=Decimal(90),
        close=Decimal(105),
        volume=Decimal(50),
        interval=Interval.H1,
    ),
    Candle(
        symbol="BTC-USD",
        timestamp=2000,
        open=Decimal(105),
        high=Decimal(115),
        low=Decimal(95),
        close=Decimal(110),
        volume=Decimal(60),
        interval=Interval.H1,
    ),
]


class TestBacktesterCli:
    """Tests for backtester CLI commands."""

    def test_default_flags(self, csv_file: Path) -> None:
        """Test CLI runs successfully with default flags."""
        result = runner.invoke(app, ["--csv", str(csv_file)])
        assert result.exit_code == 0
        assert "BTC-USD" in result.output
        assert "sma_crossover" in result.output

    def test_explicit_csv_source(self, csv_file: Path) -> None:
        """Test CLI runs with explicit --source csv."""
        result = runner.invoke(app, ["--source", "csv", "--csv", str(csv_file)])
        assert result.exit_code == 0
        assert "BTC-USD" in result.output

    def test_custom_flags(self, csv_file: Path) -> None:
        """Test CLI runs successfully with custom flags."""
        result = runner.invoke(
            app,
            [
                "--csv",
                str(csv_file),
                "--symbol",
                "ETH-USD",
                "--interval",
                "1d",
                "--capital",
                "5000",
                "--short-period",
                "5",
                "--long-period",
                "15",
            ],
        )
        assert result.exit_code == 0
        assert "ETH-USD" in result.output
        assert "5000" in result.output

    def test_missing_csv_for_csv_source(self) -> None:
        """Test CLI exits with error when --source csv but no --csv."""
        result = runner.invoke(app, ["--source", "csv"])
        assert result.exit_code != 0

    def test_invalid_strategy(self, csv_file: Path) -> None:
        """Test CLI exits with error for invalid strategy name."""
        result = runner.invoke(app, ["--csv", str(csv_file), "--strategy", "bogus"])
        assert result.exit_code != 0
        assert "Must be one of" in result.output

    def test_invalid_source(self, csv_file: Path) -> None:
        """Test CLI exits with error for invalid source."""
        result = runner.invoke(app, ["--source", "bogus", "--csv", str(csv_file)])
        assert result.exit_code != 0
        assert "Must be one of" in result.output


class TestBacktesterRevolutXSource:
    """Tests for backtester with --source revolut-x."""

    @patch("trading_tools.apps.backtester.run.RevolutXClient")
    def test_revolut_x_source(self, mock_client_cls: MagicMock) -> None:
        """Run backtest with --source revolut-x using mocked client."""
        client = AsyncMock()
        mock_client_cls.from_config.return_value = client

        with patch("trading_tools.apps.backtester.run.RevolutXCandleProvider") as mock_prov_cls:
            mock_prov_cls.return_value.get_candles = AsyncMock(return_value=_SAMPLE_CANDLES)

            result = runner.invoke(app, ["--source", "revolut-x"])

            assert result.exit_code == 0, result.output
            assert "BTC-USD" in result.output
            client.close.assert_awaited_once()
