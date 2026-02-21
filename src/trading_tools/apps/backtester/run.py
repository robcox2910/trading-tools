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
    create_benchmark_chart,
    create_comparison_chart,
    create_dashboard,
    create_monte_carlo_chart,
    create_walk_forward_chart,
    save_charts,
    show_charts,
)
from trading_tools.apps.backtester.compare import (
    format_comparison_table,
    run_comparison,
)
from trading_tools.apps.backtester.engine import BacktestEngine
from trading_tools.apps.backtester.monte_carlo import MonteCarloResult, run_monte_carlo
from trading_tools.apps.backtester.multi_asset_engine import MultiAssetEngine
from trading_tools.apps.backtester.strategies.buy_and_hold import BuyAndHoldStrategy
from trading_tools.apps.backtester.strategy_factory import STRATEGY_NAMES, build_strategy
from trading_tools.apps.backtester.walk_forward import WalkForwardResult, run_walk_forward
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
    symbols: Annotated[
        str | None, typer.Option(help="Comma-separated symbols for multi-asset mode")
    ] = None,
    benchmark: Annotated[bool, typer.Option(help="Compare against buy-and-hold")] = False,  # noqa: FBT002
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
            symbols=symbols,
            benchmark=benchmark,
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
    symbols: str | None,
    benchmark: bool,
    chart: bool,
    chart_output: Path | None,
) -> BacktestResult:
    """Orchestrate a single backtest run from resolved CLI parameters.

    Build the candle provider and strategy, construct execution and
    risk configurations, execute the backtest engine, print the result,
    optionally generate charts, and close any HTTP client resources.
    When ``symbols`` is provided, use the multi-asset engine.
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

        if symbols is not None:
            symbol_list = [s.strip() for s in symbols.split(",")]
            multi_engine = MultiAssetEngine(
                provider=provider,
                strategy=strat,
                symbols=symbol_list,
                initial_capital=resolved_capital,
                execution_config=exec_config,
                risk_config=risk_config,
            )
            result = await multi_engine.run(resolved_interval, start, end)
        else:
            engine = BacktestEngine(
                provider=provider,
                strategy=strat,
                initial_capital=resolved_capital,
                execution_config=exec_config,
                risk_config=risk_config,
            )
            result = await engine.run(symbol, resolved_interval, start, end)

        _print_result(result)

        benchmark_result: BacktestResult | None = None
        if benchmark:
            bench_engine = BacktestEngine(
                provider=provider,
                strategy=BuyAndHoldStrategy(),
                initial_capital=resolved_capital,
                execution_config=exec_config,
                risk_config=risk_config,
            )
            benchmark_result = await bench_engine.run(symbol, resolved_interval, start, end)
            _print_result(benchmark_result)

        if chart or chart_output is not None:
            _render_run_charts(result, benchmark_result, chart=chart, chart_output=chart_output)
    finally:
        if client is not None:
            await client.close()
    return result


@app.command("monte-carlo")
def monte_carlo_cmd(  # noqa: PLR0913
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
    shuffles: Annotated[int, typer.Option(help="Number of Monte Carlo shuffles")] = 1000,
    seed: Annotated[int | None, typer.Option(help="Random seed for reproducibility")] = None,
    chart: Annotated[bool, typer.Option(help="Generate interactive charts")] = False,  # noqa: FBT002
    chart_output: Annotated[
        Path | None, typer.Option(help="Save charts to HTML file instead of browser")
    ] = None,
) -> None:
    """Run a backtest then perform Monte Carlo simulation on the trades."""
    asyncio.run(
        _monte_carlo(
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
            shuffles=shuffles,
            seed=seed,
            chart=chart,
            chart_output=chart_output,
        )
    )


async def _monte_carlo(  # noqa: PLR0913
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
    shuffles: int,
    seed: int | None,
    chart: bool,
    chart_output: Path | None,
) -> None:
    """Orchestrate a Monte Carlo simulation from resolved CLI parameters.

    Run the backtest first, then pass the result to ``run_monte_carlo``
    and print the distribution table.
    """
    result = await _run(
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
        symbols=None,
        benchmark=False,
        chart=False,
        chart_output=None,
    )

    if len(result.trades) < 2:  # noqa: PLR2004
        typer.echo("Not enough trades for Monte Carlo simulation (need at least 2).")
        return

    mc_result = run_monte_carlo(result, num_shuffles=shuffles, seed=seed)
    _print_monte_carlo(mc_result)

    if chart or chart_output is not None:
        fig = create_monte_carlo_chart(mc_result)
        if chart_output is not None:
            save_charts([fig], chart_output)
            typer.echo(f"Charts saved to {chart_output}")
        else:
            show_charts([fig])


def _print_monte_carlo(mc_result: MonteCarloResult) -> None:
    """Print a formatted Monte Carlo distribution table."""
    typer.echo(f"\n{'=' * 70}")
    typer.echo(f"Monte Carlo Simulation — {mc_result.num_shuffles} shuffles")
    typer.echo(f"{'=' * 70}")

    header = f"{'Metric':<16} {'Mean':>10} {'Std':>10} {'P5':>10} {'P25':>10} {'P50':>10} {'P75':>10} {'P95':>10}"
    typer.echo(header)
    typer.echo("-" * len(header))

    for dist in mc_result.distributions:
        typer.echo(
            f"{dist.metric_name:<16} "
            f"{dist.mean:>10.4f} "
            f"{dist.std:>10.4f} "
            f"{dist.percentile_5:>10.4f} "
            f"{dist.percentile_25:>10.4f} "
            f"{dist.percentile_50:>10.4f} "
            f"{dist.percentile_75:>10.4f} "
            f"{dist.percentile_95:>10.4f}"
        )
    typer.echo(f"{'=' * 70}\n")


@app.command("walk-forward")
def walk_forward_cmd(  # noqa: PLR0913
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
    train_window: Annotated[int, typer.Option(help="Training window size in candles")] = 100,
    test_window: Annotated[int, typer.Option(help="Test window size in candles")] = 50,
    step: Annotated[int, typer.Option(help="Step size between folds")] = 50,
    sort_metric: Annotated[
        str, typer.Option(help="Metric to select best strategy")
    ] = "total_return",
    chart: Annotated[bool, typer.Option(help="Generate interactive charts")] = False,  # noqa: FBT002
    chart_output: Annotated[
        Path | None, typer.Option(help="Save charts to HTML file instead of browser")
    ] = None,
) -> None:
    """Run walk-forward optimisation across rolling train/test windows."""
    asyncio.run(
        _walk_forward(
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
            train_window=train_window,
            test_window=test_window,
            step=step,
            sort_metric=sort_metric,
            chart=chart,
            chart_output=chart_output,
        )
    )


async def _walk_forward(  # noqa: PLR0913
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
    train_window: int,
    test_window: int,
    step: int,
    sort_metric: str,
    chart: bool,
    chart_output: Path | None,
) -> None:
    """Orchestrate walk-forward optimisation from resolved CLI parameters.

    Fetch candles once, then pass to ``run_walk_forward`` which slices
    internally into train/test windows.
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

        candles = await provider.get_candles(symbol, resolved_interval, start, end)
        if not candles:
            typer.echo("No candles — cannot run walk-forward.")
            return

        wf_result = await run_walk_forward(
            candles=candles,
            symbol=symbol,
            interval=resolved_interval,
            initial_capital=resolved_capital,
            execution_config=exec_config,
            risk_config=risk_config,
            train_window=train_window,
            test_window=test_window,
            step=step,
            sort_metric=sort_metric,
            strategy_params={
                "short_period": short_period,
                "long_period": long_period,
                "period": period,
                "overbought": overbought,
                "oversold": oversold,
                "num_std": num_std,
                "fast_period": fast_period,
                "slow_period": slow_period,
                "signal_period": signal_period,
                "k_period": k_period,
                "d_period": d_period,
                "z_threshold": z_threshold,
            },
        )

        _print_walk_forward(wf_result)

        if chart or chart_output is not None:
            fig = create_walk_forward_chart(wf_result)
            if chart_output is not None:
                save_charts([fig], chart_output)
                typer.echo(f"Charts saved to {chart_output}")
            else:
                show_charts([fig])
    finally:
        if client is not None:
            await client.close()


def _print_walk_forward(wf_result: WalkForwardResult) -> None:
    """Print a formatted walk-forward result table."""
    typer.echo(f"\n{'=' * 60}")
    typer.echo(f"Walk-Forward Optimisation — {wf_result.symbol} ({wf_result.interval.value})")
    typer.echo(f"{'=' * 60}")

    header = f"{'Fold':<6} {'Strategy':<20} {'Train Return%':>14} {'Test Return%':>14}"
    typer.echo(header)
    typer.echo("-" * len(header))

    for fold in wf_result.folds:
        train_ret = float(fold.train_result.metrics.get("total_return", Decimal(0))) * 100
        test_ret = float(fold.test_result.metrics.get("total_return", Decimal(0))) * 100
        typer.echo(
            f"{fold.fold_index:<6} "
            f"{fold.best_strategy_name:<20} "
            f"{train_ret:>13.2f}% "
            f"{test_ret:>13.2f}%"
        )

    typer.echo(f"\n{'--- Aggregate Metrics ---':^60}")
    for key, value in wf_result.aggregate_metrics.items():
        typer.echo(f"  {key:20s}: {value:.6f}")
    typer.echo(f"{'=' * 60}\n")


def _render_run_charts(
    result: BacktestResult,
    benchmark_result: BacktestResult | None,
    *,
    chart: bool,
    chart_output: Path | None,
) -> None:
    """Build and display/save charts for the run command."""
    if not result.trades:
        typer.echo("No trades — skipping charts.")
        return

    figs = [create_dashboard(result)]
    if benchmark_result is not None and benchmark_result.trades:
        figs.append(create_benchmark_chart(result, benchmark_result))

    if chart_output is not None:
        save_charts(figs, chart_output)
        typer.echo(f"Charts saved to {chart_output}")
    elif chart:
        show_charts(figs)


def main() -> None:
    """Run the backtester CLI application."""
    app()


if __name__ == "__main__":
    main()
