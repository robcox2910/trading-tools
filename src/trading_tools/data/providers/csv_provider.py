"""CSV-based candle data provider for offline and testing use.

Read OHLCV candle data from a local CSV file instead of a live exchange
API. This is useful for running backtests against a fixed dataset, for
deterministic testing, or when no API credentials are available.
"""

import csv
from decimal import Decimal
from pathlib import Path

from trading_tools.core.models import Candle, Interval


class CsvCandleProvider:
    """Load candle data from a local CSV file.

    Implement the ``CandleProvider`` protocol by reading rows from a CSV
    file with columns: ``symbol``, ``timestamp``, ``open``, ``high``,
    ``low``, ``close``, ``volume``, ``interval``. Rows are filtered by
    symbol, interval, and time range so a single CSV can contain mixed
    data for multiple symbols or intervals.
    """

    def __init__(self, file_path: Path) -> None:
        """Initialize the provider with the path to the CSV file.

        Args:
            file_path: Absolute or relative path to the CSV data file.

        """
        self._file_path = file_path

    async def get_candles(
        self,
        symbol: str,
        interval: Interval,
        start_ts: int,
        end_ts: int,
    ) -> list[Candle]:
        """Load candles from the CSV file, filtered by symbol, interval, and time range.

        Read every row in the file and return only those matching the
        requested symbol, interval, and falling within ``[start_ts, end_ts]``.

        Args:
            symbol: Trading pair to filter by (e.g. ``BTC-USD``).
            interval: Candle time interval to filter by.
            start_ts: Start Unix timestamp in seconds (inclusive).
            end_ts: End Unix timestamp in seconds (inclusive).

        Returns:
            List of ``Candle`` objects matching the filter criteria.

        """
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
