"""CLI entry point for the backtester."""

import argparse
import asyncio
import sys
from decimal import Decimal
from pathlib import Path

from trading_tools.apps.backtester.engine import BacktestEngine
from trading_tools.apps.backtester.strategies.sma_crossover import (
    SmaCrossoverStrategy,
)
from trading_tools.core.config import config
from trading_tools.core.models import BacktestResult, Interval
from trading_tools.data.providers.csv_provider import CsvCandleProvider

_STRATEGIES: dict[str, type[SmaCrossoverStrategy]] = {
    "sma_crossover": SmaCrossoverStrategy,
}


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a backtest")
    parser.add_argument("--symbol", default="BTC-USD", help="Trading pair symbol")
    parser.add_argument("--interval", default=None, help="Candle interval (1m,5m,15m,1h,4h,1d,1w)")
    parser.add_argument("--csv", type=Path, help="Path to CSV candle data file")
    parser.add_argument("--capital", type=Decimal, default=None, help="Initial capital")
    parser.add_argument("--strategy", default="sma_crossover", choices=list(_STRATEGIES.keys()))
    parser.add_argument("--short-period", type=int, default=10, help="SMA short period")
    parser.add_argument("--long-period", type=int, default=20, help="SMA long period")
    parser.add_argument("--start", type=int, default=0, help="Start timestamp")
    parser.add_argument("--end", type=int, default=2**53, help="End timestamp")
    return parser.parse_args(argv)


def _resolve_interval(args: argparse.Namespace) -> Interval:
    raw = args.interval or config.get("backtester.default_interval", "1h")
    return Interval(raw)


def _resolve_capital(args: argparse.Namespace) -> Decimal:
    if args.capital is not None:
        return Decimal(str(args.capital))
    raw: object = config.get("backtester.initial_capital", 10000)
    return Decimal(str(raw))


def _print_result(result: BacktestResult) -> None:
    print(f"\n{'=' * 50}")
    print(f"Strategy:        {result.strategy_name}")
    print(f"Symbol:          {result.symbol}")
    print(f"Interval:        {result.interval.value}")
    print(f"Initial Capital: {result.initial_capital}")
    print(f"Final Capital:   {result.final_capital}")
    print(f"Trades:          {len(result.trades)}")
    if result.metrics:
        print(f"\n{'--- Metrics ---':^50}")
        for key, value in result.metrics.items():
            print(f"  {key:20s}: {value:.6f}")
    print(f"{'=' * 50}\n")


async def _run(argv: list[str] | None = None) -> BacktestResult:
    args = _parse_args(argv)

    if args.csv is None:
        print("Error: --csv is required (Revolut X live provider coming soon)")
        sys.exit(1)

    provider = CsvCandleProvider(args.csv)
    strategy = _STRATEGIES[args.strategy](args.short_period, args.long_period)
    interval = _resolve_interval(args)
    capital = _resolve_capital(args)

    engine = BacktestEngine(
        provider=provider,
        strategy=strategy,
        initial_capital=capital,
    )

    result = await engine.run(args.symbol, interval, args.start, args.end)
    _print_result(result)
    return result


def main(argv: list[str] | None = None) -> None:
    asyncio.run(_run(argv))


if __name__ == "__main__":
    main()
