"""CLI command and async helper for the ``walk-forward`` backtest command.

Run walk-forward optimisation across rolling train/test windows,
printing per-fold results and aggregate metrics.
"""

import asyncio
from pathlib import Path
from typing import Annotated

import typer

from trading_tools.apps.backtester.cli._helpers import (
    build_execution_config,
    build_provider,
    build_risk_config,
    resolve_capital,
    resolve_interval,
    validate_source,
)
from trading_tools.apps.backtester.cli._output import (
    print_walk_forward,
    render_walk_forward_charts,
)
from trading_tools.apps.backtester.walk_forward import run_walk_forward


def walk_forward_cmd(  # noqa: PLR0913
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
            volatility_sizing=volatility_sizing,
            atr_period=atr_period,
            target_risk_pct=target_risk_pct,
            circuit_breaker=circuit_breaker,
            recovery_pct=recovery_pct,
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
    volatility_sizing: bool,
    atr_period: int,
    target_risk_pct: float,
    circuit_breaker: float | None,
    recovery_pct: float | None,
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
    provider, client = build_provider(source, csv)
    try:
        resolved_interval = resolve_interval(interval)
        resolved_capital = resolve_capital(capital)

        exec_config = build_execution_config(
            maker_fee=maker_fee,
            taker_fee=taker_fee,
            slippage=slippage,
            position_size=position_size,
            volatility_sizing=volatility_sizing,
            atr_period=atr_period,
            target_risk_pct=target_risk_pct,
        )
        risk_config = build_risk_config(stop_loss, take_profit, circuit_breaker, recovery_pct)

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

        print_walk_forward(wf_result)

        if chart or chart_output is not None:
            render_walk_forward_charts(wf_result, chart=chart, chart_output=chart_output)
    finally:
        if client is not None:
            await client.close()
