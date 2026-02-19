"""Tests for the fetcher CLI."""

import csv
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from typer.testing import CliRunner

from trading_tools.apps.fetcher.run import app
from trading_tools.core.models import Candle, Interval

runner = CliRunner()

_EXPECTED_ROW_COUNT = 2

_SAMPLE_CANDLES = [
    Candle(
        symbol="BTC-USD",
        timestamp=1704067200,
        open=Decimal(42000),
        high=Decimal(42500),
        low=Decimal(41500),
        close=Decimal(42200),
        volume=Decimal(100),
        interval=Interval.H1,
    ),
    Candle(
        symbol="BTC-USD",
        timestamp=1704070800,
        open=Decimal(42200),
        high=Decimal(42800),
        low=Decimal(42000),
        close=Decimal(42600),
        volume=Decimal(120),
        interval=Interval.H1,
    ),
]


class TestFetcherCli:
    """Tests for the fetcher CLI command."""

    def test_missing_start_exits_with_error(self) -> None:
        """Exit with error when --start is not provided."""
        result = runner.invoke(app, [])
        assert result.exit_code != 0

    @patch("trading_tools.apps.fetcher.run.RevolutXCandleProvider")
    @patch("trading_tools.apps.fetcher.run.RevolutXClient")
    def test_fetch_writes_csv(
        self,
        mock_client_cls: MagicMock,
        mock_provider_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Fetch candles and write them to CSV."""
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.from_config.return_value = client

        mock_provider_cls.return_value.get_candles = AsyncMock(return_value=_SAMPLE_CANDLES)

        out = tmp_path / "out.csv"
        result = runner.invoke(
            app,
            [
                "--start",
                "2024-01-01",
                "--end",
                "2024-02-01",
                "--output",
                str(out),
            ],
        )

        assert result.exit_code == 0, result.output
        assert "2 candles" in result.output

        with out.open() as f:
            rows = list(csv.DictReader(f))

        assert len(rows) == _EXPECTED_ROW_COUNT
        assert rows[0]["symbol"] == "BTC-USD"
        assert rows[0]["interval"] == "1h"

    @patch("trading_tools.apps.fetcher.run.RevolutXCandleProvider")
    @patch("trading_tools.apps.fetcher.run.RevolutXClient")
    def test_fetch_with_unix_timestamps(
        self,
        mock_client_cls: MagicMock,
        mock_provider_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Accept raw Unix timestamps for --start and --end."""
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.from_config.return_value = client

        mock_provider_cls.return_value.get_candles = AsyncMock(return_value=_SAMPLE_CANDLES)

        out = tmp_path / "out.csv"
        result = runner.invoke(
            app,
            [
                "--start",
                "1704067200",
                "--end",
                "1706745600",
                "--output",
                str(out),
            ],
        )

        assert result.exit_code == 0, result.output

    @patch("trading_tools.apps.fetcher.run.RevolutXCandleProvider")
    @patch("trading_tools.apps.fetcher.run.RevolutXClient")
    def test_fetch_custom_symbol_and_interval(
        self,
        mock_client_cls: MagicMock,
        mock_provider_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Pass custom symbol and interval to the provider."""
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.from_config.return_value = client

        mock_provider_cls.return_value.get_candles = AsyncMock(return_value=[])

        out = tmp_path / "out.csv"
        result = runner.invoke(
            app,
            [
                "--symbol",
                "ETH-USD",
                "--interval",
                "1d",
                "--start",
                "2024-01-01",
                "--output",
                str(out),
            ],
        )

        assert result.exit_code == 0, result.output
        mock_provider_cls.return_value.get_candles.assert_awaited_once()
        call_args = mock_provider_cls.return_value.get_candles.call_args
        assert call_args[0][0] == "ETH-USD"
        assert call_args[0][1] == Interval.D1
