"""CLI command and async helper for the ``monte-carlo`` backtest command.

Run a backtest then perform Monte Carlo simulation on the trades,
printing distribution statistics and optionally rendering charts.
"""

import asyncio
from pathlib import Path
from typing import Annotated

import typer

from trading_tools.apps.backtester.cli._helpers import (
    validate_source,
    validate_strategy,
)
from trading_tools.apps.backtester.cli._output import (
    print_monte_carlo,
    render_monte_carlo_charts,
)
from trading_tools.apps.backtester.cli.run_cmd import run_backtest
from trading_tools.apps.backtester.monte_carlo import run_monte_carlo


def monte_carlo_cmd(  # noqa: PLR0913
    source: Annotated[
        str,
        typer.Option(
            help="Data source: csv, revolut-x, or binance",
            callback=validate_source,
        ),
    ] = "csv",
    csv: Annotated[Path | None, typer.Option(help="Path to CSV candle data file")] = None,
    symbol: Annotated[str, typer.Option(help="Trading pair symbol")] = "BTC-USD",
    interval: Annotated[
        str | None, typer.Option(help="Candle interval (1m,5m,15m,1h,4h,1d,1w)")
    ] = None,
    capital: Annotated[float | None, typer.Option(help="Initial capital")] = None,
    strategy: Annotated[
        str, typer.Option(help="Strategy name", callback=validate_strategy)
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
    volatility_sizing: Annotated[bool, typer.Option(help="Use ATR-based position sizing")] = False,  # noqa: FBT002
    atr_period: Annotated[int, typer.Option(help="ATR period for volatility sizing")] = 14,
    target_risk_pct: Annotated[float, typer.Option(help="Target risk per trade as decimal")] = 0.02,
    circuit_breaker: Annotated[
        float | None, typer.Option(help="Halt trading at this drawdown fraction (e.g. 0.15)")
    ] = None,
    recovery_pct: Annotated[
        float | None, typer.Option(help="Resume trading after this recovery fraction (e.g. 0.5)")
    ] = None,
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
            volatility_sizing=volatility_sizing,
            atr_period=atr_period,
            target_risk_pct=target_risk_pct,
            circuit_breaker=circuit_breaker,
            recovery_pct=recovery_pct,
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
    volatility_sizing: bool,
    atr_period: int,
    target_risk_pct: float,
    circuit_breaker: float | None,
    recovery_pct: float | None,
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
    result = await run_backtest(
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
        volatility_sizing=volatility_sizing,
        atr_period=atr_period,
        target_risk_pct=target_risk_pct,
        circuit_breaker=circuit_breaker,
        recovery_pct=recovery_pct,
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
    print_monte_carlo(mc_result)

    if chart or chart_output is not None:
        render_monte_carlo_charts(mc_result, chart=chart, chart_output=chart_output)
