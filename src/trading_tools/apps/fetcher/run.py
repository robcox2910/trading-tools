"""CLI entry point for the candle data fetcher.

Provide a Typer CLI command that fetches historical OHLCV candle data
from an exchange API (Revolut X or Binance) and writes the result to a
CSV file compatible with the ``CsvCandleProvider``.
"""

import asyncio
import csv
import time
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from trading_tools.clients.binance.client import BinanceClient
from trading_tools.clients.revolut_x.client import RevolutXClient
from trading_tools.core.models import Candle, Interval
from trading_tools.core.timestamps import parse_timestamp
from trading_tools.data.providers.binance import BinanceCandleProvider
from trading_tools.data.providers.revolut_x import RevolutXCandleProvider

if TYPE_CHECKING:
    from trading_tools.core.protocols import CandleProvider

app = typer.Typer(help="Fetch candle data from exchange APIs")

_VALID_SOURCES = ("revolut-x", "binance")


def _validate_source(value: str) -> str:
    """Validate that the data source is one of the supported exchange APIs.

    Raise ``typer.BadParameter`` if the source is not recognised.
    """
    if value not in _VALID_SOURCES:
        raise typer.BadParameter(f"Must be one of: {', '.join(_VALID_SOURCES)}")
    return value


_CSV_COLUMNS = ("symbol", "timestamp", "open", "high", "low", "close", "volume", "interval")


def _parse_ts_option(value: str) -> int:
    """Parse a CLI timestamp option, raising BadParameter on failure."""
    try:
        return parse_timestamp(value)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _write_csv(candles: list[Candle], output: Path) -> None:
    """Write candles to a CSV file in CsvCandleProvider format."""
    with output.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(_CSV_COLUMNS)
        for c in candles:
            writer.writerow(
                (
                    c.symbol,
                    c.timestamp,
                    c.open,
                    c.high,
                    c.low,
                    c.close,
                    c.volume,
                    c.interval.value,
                )
            )


@app.command()
def fetch(
    symbol: Annotated[str, typer.Option(help="Trading pair symbol")] = "BTC-USD",
    interval: Annotated[
        str,
        typer.Option(
            help="Candle interval (1m,5m,15m,1h,4h,1d,1w)",
        ),
    ] = "1h",
    start: Annotated[str, typer.Option(help="Start date (ISO 8601) or Unix timestamp")] = "",
    end: Annotated[
        str,
        typer.Option(
            help="End date (ISO 8601) or Unix timestamp; defaults to now",
        ),
    ] = "",
    output: Annotated[Path, typer.Option(help="Output CSV file path")] = Path("candles.csv"),
    source: Annotated[
        str,
        typer.Option(
            help="Data source: revolut-x or binance",
            callback=_validate_source,
        ),
    ] = "revolut-x",
) -> None:
    """Fetch candle data from an exchange API and save to CSV."""
    if not start:
        raise typer.BadParameter("--start is required", param_hint="'--start'")

    start_ts = _parse_ts_option(start)
    end_ts = _parse_ts_option(end) if end else int(time.time())

    asyncio.run(
        _fetch(
            symbol=symbol,
            interval=interval,
            start_ts=start_ts,
            end_ts=end_ts,
            output=output,
            source=source,
        )
    )


async def _fetch(
    *,
    symbol: str,
    interval: str,
    start_ts: int,
    end_ts: int,
    output: Path,
    source: str,
) -> list[Candle]:
    """Fetch candles from the selected API and write them to CSV."""
    resolved_interval = Interval(interval)

    if source == "binance":
        async with BinanceClient() as client:
            provider: CandleProvider = BinanceCandleProvider(client)
            candles = await provider.get_candles(symbol, resolved_interval, start_ts, end_ts)
    else:
        async with RevolutXClient.from_config() as client:
            provider = RevolutXCandleProvider(client)
            candles = await provider.get_candles(symbol, resolved_interval, start_ts, end_ts)

    _write_csv(candles, output)
    typer.echo(f"Wrote {len(candles)} candles to {output}")
    return candles


def main() -> None:
    """Run the fetcher CLI application."""
    app()


if __name__ == "__main__":
    main()
