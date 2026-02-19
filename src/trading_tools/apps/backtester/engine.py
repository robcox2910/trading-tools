"""Backtest engine that orchestrates strategy evaluation against candle data."""

from decimal import Decimal

from trading_tools.apps.backtester.metrics import calculate_metrics
from trading_tools.apps.backtester.portfolio import Portfolio
from trading_tools.core.models import BacktestResult, Candle, Interval
from trading_tools.core.protocols import CandleProvider, TradingStrategy


class BacktestEngine:
    """Runs a trading strategy against historical candle data."""

    def __init__(
        self,
        provider: CandleProvider,
        strategy: TradingStrategy,
        initial_capital: Decimal,
    ) -> None:
        """Initialize the backtest engine."""
        self._provider = provider
        self._strategy = strategy
        self._initial_capital = initial_capital

    async def run(
        self,
        symbol: str,
        interval: Interval,
        start_ts: int,
        end_ts: int,
    ) -> BacktestResult:
        """Execute the backtest and return results."""
        candles = await self._provider.get_candles(symbol, interval, start_ts, end_ts)
        if not candles:
            return self._empty_result(symbol, interval)

        portfolio = Portfolio(self._initial_capital)
        history: list[Candle] = []

        for candle in candles:
            signal = self._strategy.on_candle(candle, history)
            if signal is not None:
                portfolio.process_signal(signal, candle.close, candle.timestamp)
            history.append(candle)

        last = candles[-1]
        portfolio.force_close(last.close, last.timestamp)

        trades = portfolio.trades
        metrics = calculate_metrics(trades, self._initial_capital, portfolio.capital)

        return BacktestResult(
            strategy_name=self._strategy.name,
            symbol=symbol,
            interval=interval,
            initial_capital=self._initial_capital,
            final_capital=portfolio.capital,
            trades=tuple(trades),
            metrics=metrics,
        )

    def _empty_result(self, symbol: str, interval: Interval) -> BacktestResult:
        return BacktestResult(
            strategy_name=self._strategy.name,
            symbol=symbol,
            interval=interval,
            initial_capital=self._initial_capital,
            final_capital=self._initial_capital,
            trades=(),
        )
