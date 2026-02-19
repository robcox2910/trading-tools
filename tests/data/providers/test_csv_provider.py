"""Tests for CSV candle provider."""

from decimal import Decimal
from pathlib import Path

import pytest

from trading_tools.core.models import Candle, Interval
from trading_tools.core.protocols import CandleProvider
from trading_tools.data.providers.csv_provider import CsvCandleProvider

CSV_HEADER = "symbol,timestamp,open,high,low,close,volume,interval\n"
CSV_ROW_1 = "BTC-USD,1000,100,110,90,105,50,1h\n"
CSV_ROW_2 = "BTC-USD,2000,105,115,95,110,60,1h\n"
CSV_ROW_3 = "ETH-USD,1000,200,210,190,205,30,1h\n"
CSV_ROW_4 = "BTC-USD,3000,110,120,100,115,40,1d\n"

EXPECTED_BTC_H1_COUNT = 2
EXPECTED_TIMESTAMP = 2000


@pytest.fixture
def csv_file(tmp_path: Path) -> Path:
    """Create a temporary CSV file with candle data."""
    f = tmp_path / "candles.csv"
    f.write_text(CSV_HEADER + CSV_ROW_1 + CSV_ROW_2 + CSV_ROW_3 + CSV_ROW_4)
    return f


class TestCsvCandleProvider:
    """Tests for CsvCandleProvider."""

    def test_satisfies_protocol(self, csv_file: Path) -> None:
        """Test that CsvCandleProvider satisfies CandleProvider protocol."""
        assert isinstance(CsvCandleProvider(csv_file), CandleProvider)

    @pytest.mark.asyncio
    async def test_load_candles(self, csv_file: Path) -> None:
        """Test loading candles filtered by symbol and interval."""
        provider = CsvCandleProvider(csv_file)
        candles = await provider.get_candles("BTC-USD", Interval.H1, 0, 5000)
        assert len(candles) == EXPECTED_BTC_H1_COUNT
        assert all(isinstance(c, Candle) for c in candles)
        assert candles[0].close == Decimal(105)
        assert candles[1].close == Decimal(110)

    @pytest.mark.asyncio
    async def test_filter_by_symbol(self, csv_file: Path) -> None:
        """Test filtering candles by symbol."""
        provider = CsvCandleProvider(csv_file)
        candles = await provider.get_candles("ETH-USD", Interval.H1, 0, 5000)
        assert len(candles) == 1
        assert candles[0].symbol == "ETH-USD"

    @pytest.mark.asyncio
    async def test_filter_by_interval(self, csv_file: Path) -> None:
        """Test filtering candles by interval."""
        provider = CsvCandleProvider(csv_file)
        candles = await provider.get_candles("BTC-USD", Interval.D1, 0, 5000)
        assert len(candles) == 1
        assert candles[0].interval == Interval.D1

    @pytest.mark.asyncio
    async def test_filter_by_time_range(self, csv_file: Path) -> None:
        """Test filtering candles by time range."""
        provider = CsvCandleProvider(csv_file)
        candles = await provider.get_candles("BTC-USD", Interval.H1, 1500, 5000)
        assert len(candles) == 1
        assert candles[0].timestamp == EXPECTED_TIMESTAMP

    @pytest.mark.asyncio
    async def test_empty_result(self, csv_file: Path) -> None:
        """Test empty result for non-matching symbol."""
        provider = CsvCandleProvider(csv_file)
        candles = await provider.get_candles("XRP-USD", Interval.H1, 0, 5000)
        assert candles == []
