"""CLI entry point for the backtester.

Wire together the data source (CSV, Revolut X, or Binance), a chosen
trading strategy, and the backtest engine. Parse CLI options via Typer,
resolve defaults from the YAML config, run the backtest, and print
the resulting performance metrics to the terminal.
"""

import asyncio
from decimal import Decimal
from pathlib import Path
from typing import Annotated

import typer

from trading_tools.apps.backtester.charts import (
    create_comparison_chart,
    create_dashboard,
    save_charts,
    show_charts,
)
from trading_tools.apps.backtester.compare import (
    format_comparison_table,
    run_comparison,
)
from trading_tools.apps.backtester.engine import BacktestEngine
from trading_tools.apps.backtester.strategy_factory import STRATEGY_NAMES, build_strategy
from trading_tools.clients.binance.client import BinanceClient
from trading_tools.clients.revolut_x.client import RevolutXClient
from trading_tools.core.config import config
from trading_tools.core.models import BacktestResult, ExecutionConfig, Interval, RiskConfig
from trading_tools.core.protocols import CandleProvider
from trading_tools.data.providers.binance import BinanceCandleProvider
from trading_tools.data.providers.csv_provider import CsvCandleProvider
from trading_tools.data.providers.revolut_x import RevolutXCandleProvider

_VALID_SOURCES = ("csv", "revolut-x", "binance")

app = typer.Typer(help="Run a backtest")


def _validate_strategy(value: str) -> str:
    """Validate that the strategy name is one of the known strategy identifiers.

    Raise ``typer.BadParameter`` if the name is not recognised.
    """
    if value not in STRATEGY_NAMES:
        raise typer.BadParameter(f"Must be one of: {', '.join(STRATEGY_NAMES)}")
    return value


def _resolve_interval(raw: str | None) -> Interval:
    """Resolve the candle interval from the CLI option or YAML config default.

    Fall back to ``1h`` when neither the CLI option nor the config key
    ``backtester.default_interval`` is set.
    """
    value = raw or config.get("backtester.default_interval", "1h")
    return Interval(str(value))


def _resolve_capital(capital: float | None) -> Decimal:
    """Resolve the initial capital from the CLI option or YAML config default.

    Fall back to ``10000`` when neither the CLI option nor the config key
    ``backtester.initial_capital`` is set. Convert to ``Decimal`` for
    lossless arithmetic throughout the backtester.
    """
    if capital is not None:
        return Decimal(str(capital))
    raw: object = config.get("backtester.initial_capital", 10000)
    return Decimal(str(raw))


def _validate_source(value: str) -> str:
    """Validate that the data source is one of the supported providers.

    Raise ``typer.BadParameter`` if the source is not recognised.
    """
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


def _print_result(result: BacktestResult) -> None:
    """Print a formatted summary of the backtest result to the terminal."""
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
def compare(  # noqa: PLR0913
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
    short_period: Annotated[int, typer.Option(help="Short EMA/SMA period")] = 10,
    long_period: Annotated[int, typer.Option(help="Long EMA/SMA period")] = 20,
    period: Annotated[
        int, typer.Option(help="Period for RSI, Bollinger, VWAP, Donchian, or Mean Reversion")
    ] = 14,
    overbought: Annotated[int, typer.Option(help="RSI/Stochastic overbought threshold")] = 70,
    oversold: Annotated[int, typer.Option(help="RSI/Stochastic oversold threshold")] = 30,
    num_std: Annotated[float, typer.Option(help="Bollinger Band std deviations")] = 2.0,
    fast_period: Annotated[int, typer.Option(help="MACD fast EMA period")] = 12,
    slow_period: Annotated[int, typer.Option(help="MACD slow EMA period")] = 26,
    signal_period: Annotated[int, typer.Option(help="MACD signal EMA period")] = 9,
    k_period: Annotated[int, typer.Option(help="Stochastic %K period")] = 14,
    d_period: Annotated[int, typer.Option(help="Stochastic %D period")] = 3,
    z_threshold: Annotated[float, typer.Option(help="Mean reversion z-score threshold")] = 2.0,
    maker_fee: Annotated[float, typer.Option(help="Maker fee as decimal (e.g. 0.001)")] = 0.0,
    taker_fee: Annotated[float, typer.Option(help="Taker fee as decimal (e.g. 0.001)")] = 0.0,
    slippage: Annotated[float, typer.Option(help="Slippage as decimal (e.g. 0.0005)")] = 0.0,
    stop_loss: Annotated[float | None, typer.Option(help="Stop-loss threshold as decimal")] = None,
    take_profit: Annotated[
        float | None, typer.Option(help="Take-profit threshold as decimal")
    ] = None,
    position_size: Annotated[float, typer.Option(help="Fraction of capital per trade (0-1)")] = 1.0,
    start: Annotated[int, typer.Option(help="Start timestamp")] = 0,
    end: Annotated[int, typer.Option(help="End timestamp")] = 2**53,
    sort_by: Annotated[str, typer.Option(help="Metric to rank by")] = "total_return",
    chart: Annotated[bool, typer.Option(help="Generate interactive charts")] = False,  # noqa: FBT002
    chart_output: Annotated[
        Path | None, typer.Option(help="Save charts to HTML file instead of browser")
    ] = None,
) -> None:
    """Run all strategies on the same data and display a ranked comparison table."""
    asyncio.run(
        _compare(
            source=source,
            csv=csv,
            symbol=symbol,
            interval=interval,
            capital=capital,
            short_period=short_period,
            long_period=long_period,
            period=period,
            overbought=overbought,
            oversold=oversold,
            num_std=num_std,
            fast_period=fast_period,
            slow_period=slow_period,
            signal_period=signal_period,
            k_period=k_period,
            d_period=d_period,
            z_threshold=z_threshold,
            maker_fee=maker_fee,
            taker_fee=taker_fee,
            slippage=slippage,
            stop_loss=stop_loss,
            take_profit=take_profit,
            position_size=position_size,
            start=start,
            end=end,
            sort_by=sort_by,
            chart=chart,
            chart_output=chart_output,
        )
    )


async def _compare(  # noqa: PLR0913
    *,
    source: str,
    csv: Path | None,
    symbol: str,
    interval: str | None,
    capital: float | None,
    short_period: int,
    long_period: int,
    period: int,
    overbought: int,
    oversold: int,
    num_std: float,
    fast_period: int,
    slow_period: int,
    signal_period: int,
    k_period: int,
    d_period: int,
    z_threshold: float,
    maker_fee: float,
    taker_fee: float,
    slippage: float,
    stop_loss: float | None,
    take_profit: float | None,
    position_size: float,
    start: int,
    end: int,
    sort_by: str,
    chart: bool,
    chart_output: Path | None,
) -> None:
    """Orchestrate a multi-strategy comparison from resolved CLI parameters.

    Build the candle provider and configuration objects, run all strategies
    via ``run_comparison``, and print the ranked comparison table.
    Optionally generate an interactive comparison chart.
    """
    provider, client = _build_provider(source, csv)
    try:
        resolved_interval = _resolve_interval(interval)
        resolved_capital = _resolve_capital(capital)

        exec_config = ExecutionConfig(
            maker_fee_pct=Decimal(str(maker_fee)),
            taker_fee_pct=Decimal(str(taker_fee)),
            slippage_pct=Decimal(str(slippage)),
            position_size_pct=Decimal(str(position_size)),
        )
        risk_config = RiskConfig(
            stop_loss_pct=Decimal(str(stop_loss)) if stop_loss is not None else None,
            take_profit_pct=Decimal(str(take_profit)) if take_profit is not None else None,
        )

        results = await run_comparison(
            provider=provider,
            symbol=symbol,
            interval=resolved_interval,
            capital=resolved_capital,
            execution_config=exec_config,
            risk_config=risk_config,
            start=start,
            end=end,
            short_period=short_period,
            long_period=long_period,
            period=period,
            overbought=overbought,
            oversold=oversold,
            num_std=num_std,
            fast_period=fast_period,
            slow_period=slow_period,
            signal_period=signal_period,
            k_period=k_period,
            d_period=d_period,
            z_threshold=z_threshold,
        )

        table = format_comparison_table(results, sort_by=sort_by)
        typer.echo(f"\nStrategy Comparison: {symbol} ({resolved_interval.value})")
        typer.echo(table)

        if chart or chart_output is not None:
            has_trades = any(r.trades for r in results)
            if has_trades:
                fig = create_comparison_chart(results)
                if chart_output is not None:
                    save_charts([fig], chart_output)
                    typer.echo(f"Charts saved to {chart_output}")
                else:
                    show_charts([fig])
            else:
                typer.echo("No trades — skipping charts.")
    finally:
        if client is not None:
            await client.close()


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
    period: Annotated[
        int, typer.Option(help="Period for RSI, Bollinger, VWAP, Donchian, or Mean Reversion")
    ] = 14,
    overbought: Annotated[int, typer.Option(help="RSI/Stochastic overbought threshold")] = 70,
    oversold: Annotated[int, typer.Option(help="RSI/Stochastic oversold threshold")] = 30,
    num_std: Annotated[float, typer.Option(help="Bollinger Band std deviations")] = 2.0,
    fast_period: Annotated[int, typer.Option(help="MACD fast EMA period")] = 12,
    slow_period: Annotated[int, typer.Option(help="MACD slow EMA period")] = 26,
    signal_period: Annotated[int, typer.Option(help="MACD signal EMA period")] = 9,
    k_period: Annotated[int, typer.Option(help="Stochastic %K period")] = 14,
    d_period: Annotated[int, typer.Option(help="Stochastic %D period")] = 3,
    z_threshold: Annotated[float, typer.Option(help="Mean reversion z-score threshold")] = 2.0,
    maker_fee: Annotated[float, typer.Option(help="Maker fee as decimal (e.g. 0.001)")] = 0.0,
    taker_fee: Annotated[float, typer.Option(help="Taker fee as decimal (e.g. 0.001)")] = 0.0,
    slippage: Annotated[float, typer.Option(help="Slippage as decimal (e.g. 0.0005)")] = 0.0,
    stop_loss: Annotated[float | None, typer.Option(help="Stop-loss threshold as decimal")] = None,
    take_profit: Annotated[
        float | None, typer.Option(help="Take-profit threshold as decimal")
    ] = None,
    position_size: Annotated[float, typer.Option(help="Fraction of capital per trade (0-1)")] = 1.0,
    start: Annotated[int, typer.Option(help="Start timestamp")] = 0,
    end: Annotated[int, typer.Option(help="End timestamp")] = 2**53,
    chart: Annotated[bool, typer.Option(help="Generate interactive charts")] = False,  # noqa: FBT002
    chart_output: Annotated[
        Path | None, typer.Option(help="Save charts to HTML file instead of browser")
    ] = None,
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
            k_period=k_period,
            d_period=d_period,
            z_threshold=z_threshold,
            maker_fee=maker_fee,
            taker_fee=taker_fee,
            slippage=slippage,
            stop_loss=stop_loss,
            take_profit=take_profit,
            position_size=position_size,
            start=start,
            end=end,
            chart=chart,
            chart_output=chart_output,
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
    k_period: int,
    d_period: int,
    z_threshold: float,
    maker_fee: float,
    taker_fee: float,
    slippage: float,
    stop_loss: float | None,
    take_profit: float | None,
    position_size: float,
    start: int,
    end: int,
    chart: bool,
    chart_output: Path | None,
) -> BacktestResult:
    """Orchestrate a single backtest run from resolved CLI parameters.

    Build the candle provider and strategy, construct execution and
    risk configurations, execute the backtest engine, print the result,
    optionally generate charts, and close any HTTP client resources.
    """
    provider, client = _build_provider(source, csv)
    try:
        strat = build_strategy(
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
            k_period=k_period,
            d_period=d_period,
            z_threshold=z_threshold,
        )
        resolved_interval = _resolve_interval(interval)
        resolved_capital = _resolve_capital(capital)

        exec_config = ExecutionConfig(
            maker_fee_pct=Decimal(str(maker_fee)),
            taker_fee_pct=Decimal(str(taker_fee)),
            slippage_pct=Decimal(str(slippage)),
            position_size_pct=Decimal(str(position_size)),
        )
        risk_config = RiskConfig(
            stop_loss_pct=Decimal(str(stop_loss)) if stop_loss is not None else None,
            take_profit_pct=Decimal(str(take_profit)) if take_profit is not None else None,
        )

        engine = BacktestEngine(
            provider=provider,
            strategy=strat,
            initial_capital=resolved_capital,
            execution_config=exec_config,
            risk_config=risk_config,
        )

        result = await engine.run(symbol, resolved_interval, start, end)
        _print_result(result)

        if chart or chart_output is not None:
            if result.trades:
                fig = create_dashboard(result)
                if chart_output is not None:
                    save_charts([fig], chart_output)
                    typer.echo(f"Charts saved to {chart_output}")
                else:
                    show_charts([fig])
            else:
                typer.echo("No trades — skipping charts.")
    finally:
        if client is not None:
            await client.close()
    return result


def main() -> None:
    """Run the backtester CLI application."""
    app()


if __name__ == "__main__":
    main()
