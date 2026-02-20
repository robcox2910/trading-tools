"""CLI entry point for the backtester."""

import asyncio
from decimal import Decimal
from pathlib import Path
from typing import Annotated

import typer

from trading_tools.apps.backtester.engine import BacktestEngine
from trading_tools.apps.backtester.strategies.bollinger import BollingerStrategy
from trading_tools.apps.backtester.strategies.ema_crossover import (
    EmaCrossoverStrategy,
)
from trading_tools.apps.backtester.strategies.macd import MacdStrategy
from trading_tools.apps.backtester.strategies.rsi import RsiStrategy
from trading_tools.apps.backtester.strategies.sma_crossover import (
    SmaCrossoverStrategy,
)
from trading_tools.clients.binance.client import BinanceClient
from trading_tools.clients.revolut_x.client import RevolutXClient
from trading_tools.core.config import config
from trading_tools.core.models import BacktestResult, Interval
from trading_tools.core.protocols import CandleProvider, TradingStrategy
from trading_tools.data.providers.binance import BinanceCandleProvider
from trading_tools.data.providers.csv_provider import CsvCandleProvider
from trading_tools.data.providers.revolut_x import RevolutXCandleProvider

_STRATEGY_NAMES = ("sma_crossover", "ema_crossover", "rsi", "bollinger", "macd")

_VALID_SOURCES = ("csv", "revolut-x", "binance")

app = typer.Typer(help="Run a backtest")


def _validate_strategy(value: str) -> str:
    if value not in _STRATEGY_NAMES:
        raise typer.BadParameter(f"Must be one of: {', '.join(_STRATEGY_NAMES)}")
    return value


def _resolve_interval(raw: str | None) -> Interval:
    value = raw or config.get("backtester.default_interval", "1h")
    return Interval(str(value))


def _resolve_capital(capital: float | None) -> Decimal:
    if capital is not None:
        return Decimal(str(capital))
    raw: object = config.get("backtester.initial_capital", 10000)
    return Decimal(str(raw))


def _validate_source(value: str) -> str:
    if value not in _VALID_SOURCES:
        raise typer.BadParameter(f"Must be one of: {', '.join(_VALID_SOURCES)}")
    return value


def _build_provider(
    source: str,
    csv_path: Path | None,
) -> tuple[CandleProvider, RevolutXClient | BinanceClient | None]:
    """Build a candle provider based on the selected source.

    Return the provider and an optional client that must be closed after use.
    """
    if source == "revolut-x":
        client: RevolutXClient | BinanceClient = RevolutXClient.from_config()
        return RevolutXCandleProvider(client), client

    if source == "binance":
        binance_client = BinanceClient()
        return BinanceCandleProvider(binance_client), binance_client

    if csv_path is None:
        raise typer.BadParameter("--csv is required when --source is csv", param_hint="'--csv'")
    return CsvCandleProvider(csv_path), None


def _build_strategy(
    name: str,
    *,
    short_period: int,
    long_period: int,
    period: int,
    overbought: int,
    oversold: int,
    num_std: float,
    fast_period: int,
    slow_period: int,
    signal_period: int,
) -> TradingStrategy:
    """Build a strategy instance from CLI parameters."""
    if name == "sma_crossover":
        return SmaCrossoverStrategy(short_period, long_period)
    if name == "ema_crossover":
        return EmaCrossoverStrategy(short_period, long_period)
    if name == "rsi":
        return RsiStrategy(period=period, overbought=overbought, oversold=oversold)
    if name == "bollinger":
        return BollingerStrategy(period=period, num_std=num_std)
    if name == "macd":
        return MacdStrategy(
            fast_period=fast_period,
            slow_period=slow_period,
            signal_period=signal_period,
        )
    msg = f"Unknown strategy: {name}"
    raise typer.BadParameter(msg)


def _print_result(result: BacktestResult) -> None:
    typer.echo(f"\n{'=' * 50}")
    typer.echo(f"Strategy:        {result.strategy_name}")
    typer.echo(f"Symbol:          {result.symbol}")
    typer.echo(f"Interval:        {result.interval.value}")
    typer.echo(f"Initial Capital: {result.initial_capital}")
    typer.echo(f"Final Capital:   {result.final_capital}")
    typer.echo(f"Trades:          {len(result.trades)}")
    if result.metrics:
        typer.echo(f"\n{'--- Metrics ---':^50}")
        for key, value in result.metrics.items():
            typer.echo(f"  {key:20s}: {value:.6f}")
    typer.echo(f"{'=' * 50}\n")


@app.command()
def run(  # noqa: PLR0913
    source: Annotated[
        str,
        typer.Option(
            help="Data source: csv, revolut-x, or binance",
            callback=_validate_source,
        ),
    ] = "csv",
    csv: Annotated[Path | None, typer.Option(help="Path to CSV candle data file")] = None,
    symbol: Annotated[str, typer.Option(help="Trading pair symbol")] = "BTC-USD",
    interval: Annotated[
        str | None, typer.Option(help="Candle interval (1m,5m,15m,1h,4h,1d,1w)")
    ] = None,
    capital: Annotated[float | None, typer.Option(help="Initial capital")] = None,
    strategy: Annotated[
        str, typer.Option(help="Strategy name", callback=_validate_strategy)
    ] = "sma_crossover",
    short_period: Annotated[int, typer.Option(help="Short EMA/SMA period")] = 10,
    long_period: Annotated[int, typer.Option(help="Long EMA/SMA period")] = 20,
    period: Annotated[int, typer.Option(help="Period for RSI or Bollinger")] = 14,
    overbought: Annotated[int, typer.Option(help="RSI overbought threshold")] = 70,
    oversold: Annotated[int, typer.Option(help="RSI oversold threshold")] = 30,
    num_std: Annotated[float, typer.Option(help="Bollinger Band std deviations")] = 2.0,
    fast_period: Annotated[int, typer.Option(help="MACD fast EMA period")] = 12,
    slow_period: Annotated[int, typer.Option(help="MACD slow EMA period")] = 26,
    signal_period: Annotated[int, typer.Option(help="MACD signal EMA period")] = 9,
    start: Annotated[int, typer.Option(help="Start timestamp")] = 0,
    end: Annotated[int, typer.Option(help="End timestamp")] = 2**53,
) -> None:
    """Run a backtest against historical candle data."""
    asyncio.run(
        _run(
            source=source,
            csv=csv,
            symbol=symbol,
            interval=interval,
            capital=capital,
            strategy=strategy,
            short_period=short_period,
            long_period=long_period,
            period=period,
            overbought=overbought,
            oversold=oversold,
            num_std=num_std,
            fast_period=fast_period,
            slow_period=slow_period,
            signal_period=signal_period,
            start=start,
            end=end,
        )
    )


async def _run(  # noqa: PLR0913
    *,
    source: str,
    csv: Path | None,
    symbol: str,
    interval: str | None,
    capital: float | None,
    strategy: str,
    short_period: int,
    long_period: int,
    period: int,
    overbought: int,
    oversold: int,
    num_std: float,
    fast_period: int,
    slow_period: int,
    signal_period: int,
    start: int,
    end: int,
) -> BacktestResult:
    provider, client = _build_provider(source, csv)
    try:
        strat = _build_strategy(
            strategy,
            short_period=short_period,
            long_period=long_period,
            period=period,
            overbought=overbought,
            oversold=oversold,
            num_std=num_std,
            fast_period=fast_period,
            slow_period=slow_period,
            signal_period=signal_period,
        )
        resolved_interval = _resolve_interval(interval)
        resolved_capital = _resolve_capital(capital)

        engine = BacktestEngine(
            provider=provider,
            strategy=strat,
            initial_capital=resolved_capital,
        )

        result = await engine.run(symbol, resolved_interval, start, end)
        _print_result(result)
    finally:
        if client is not None:
            await client.close()
    return result


def main() -> None:
    """Run the backtester CLI application."""
    app()


if __name__ == "__main__":
    main()
