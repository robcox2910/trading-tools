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


@pytest.fixture
def csv_file(tmp_path: Path) -> Path:
    f = tmp_path / "candles.csv"
    f.write_text(CSV_HEADER + CSV_ROW_1 + CSV_ROW_2 + CSV_ROW_3 + CSV_ROW_4)
    return f


class TestCsvCandleProvider:
    def test_satisfies_protocol(self, csv_file: Path) -> None:
        assert isinstance(CsvCandleProvider(csv_file), CandleProvider)

    @pytest.mark.asyncio
    async def test_load_candles(self, csv_file: Path) -> None:
        provider = CsvCandleProvider(csv_file)
        candles = await provider.get_candles("BTC-USD", Interval.H1, 0, 5000)
        assert len(candles) == 2
        assert all(isinstance(c, Candle) for c in candles)
        assert candles[0].close == Decimal("105")
        assert candles[1].close == Decimal("110")

    @pytest.mark.asyncio
    async def test_filter_by_symbol(self, csv_file: Path) -> None:
        provider = CsvCandleProvider(csv_file)
        candles = await provider.get_candles("ETH-USD", Interval.H1, 0, 5000)
        assert len(candles) == 1
        assert candles[0].symbol == "ETH-USD"

    @pytest.mark.asyncio
    async def test_filter_by_interval(self, csv_file: Path) -> None:
        provider = CsvCandleProvider(csv_file)
        candles = await provider.get_candles("BTC-USD", Interval.D1, 0, 5000)
        assert len(candles) == 1
        assert candles[0].interval == Interval.D1

    @pytest.mark.asyncio
    async def test_filter_by_time_range(self, csv_file: Path) -> None:
        provider = CsvCandleProvider(csv_file)
        candles = await provider.get_candles("BTC-USD", Interval.H1, 1500, 5000)
        assert len(candles) == 1
        assert candles[0].timestamp == 2000

    @pytest.mark.asyncio
    async def test_empty_result(self, csv_file: Path) -> None:
        provider = CsvCandleProvider(csv_file)
        candles = await provider.get_candles("XRP-USD", Interval.H1, 0, 5000)
        assert candles == []
