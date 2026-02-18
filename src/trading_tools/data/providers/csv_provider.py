"""CSV-based candle data provider for offline/testing use."""

import csv
from decimal import Decimal
from pathlib import Path

from trading_tools.core.models import Candle, Interval


class CsvCandleProvider:
    """Loads candle data from CSV files.

    Expected CSV columns: symbol, timestamp, open, high, low, close, volume, interval
    """

    def __init__(self, file_path: Path) -> None:
        self._file_path = file_path

    async def get_candles(
        self,
        symbol: str,
        interval: Interval,
        start_ts: int,
        end_ts: int,
    ) -> list[Candle]:
        """Load candles from CSV, filtered by symbol, interval, and time range."""
        candles: list[Candle] = []
        with self._file_path.open() as f:
            reader = csv.DictReader(f)
            for row in reader:
                ts = int(row["timestamp"])
                if row["symbol"] != symbol:
                    continue
                if row["interval"] != interval.value:
                    continue
                if ts < start_ts or ts > end_ts:
                    continue
                candles.append(
                    Candle(
                        symbol=row["symbol"],
                        timestamp=ts,
                        open=Decimal(row["open"]),
                        high=Decimal(row["high"]),
                        low=Decimal(row["low"]),
                        close=Decimal(row["close"]),
                        volume=Decimal(row["volume"]),
                        interval=Interval(row["interval"]),
                    )
                )
        return candles
