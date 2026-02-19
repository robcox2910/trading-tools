"""Tests for the backtester CLI."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from trading_tools.apps.backtester.run import app

CSV_HEADER = "symbol,timestamp,open,high,low,close,volume,interval\n"
CSV_ROW_1 = "BTC-USD,1000,100,110,90,105,50,1h\n"
CSV_ROW_2 = "BTC-USD,2000,105,115,95,110,60,1h\n"

runner = CliRunner()


@pytest.fixture
def csv_file(tmp_path: Path) -> Path:
    """Create a temporary CSV file with candle data."""
    f = tmp_path / "candles.csv"
    f.write_text(CSV_HEADER + CSV_ROW_1 + CSV_ROW_2)
    return f


class TestBacktesterCli:
    """Tests for backtester CLI commands."""

    def test_default_flags(self, csv_file: Path) -> None:
        """Test CLI runs successfully with default flags."""
        result = runner.invoke(app, ["--csv", str(csv_file)])
        assert result.exit_code == 0
        assert "BTC-USD" in result.output
        assert "sma_crossover" in result.output

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

    def test_missing_csv_exits_with_error(self) -> None:
        """Test CLI exits with error when CSV is not provided."""
        result = runner.invoke(app, [])
        assert result.exit_code != 0

    def test_invalid_strategy(self, csv_file: Path) -> None:
        """Test CLI exits with error for invalid strategy name."""
        result = runner.invoke(app, ["--csv", str(csv_file), "--strategy", "bogus"])
        assert result.exit_code != 0
        assert "Must be one of" in result.output
