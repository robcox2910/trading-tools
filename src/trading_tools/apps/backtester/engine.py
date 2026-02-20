"""Backtest engine that orchestrates strategy evaluation against candle data.

Fetch candles from a ``CandleProvider``, feed them one-by-one to a
``TradingStrategy``, and track the resulting trades and portfolio state.
Return a ``BacktestResult`` containing final capital, trade history,
and performance metrics.
"""

from decimal import Decimal

from trading_tools.apps.backtester.metrics import calculate_metrics
from trading_tools.apps.backtester.portfolio import Portfolio
from trading_tools.core.models import BacktestResult, Candle, Interval
from trading_tools.core.protocols import CandleProvider, TradingStrategy


class BacktestEngine:
    """Run a trading strategy against historical candle data.

    Coordinate a ``CandleProvider`` (data source), ``TradingStrategy``
    (signal generation), and ``Portfolio`` (position tracking) to simulate
    trading over a historical period.
    """

    def __init__(
        self,
        provider: CandleProvider,
        strategy: TradingStrategy,
        initial_capital: Decimal,
    ) -> None:
        """Initialize the backtest engine.

        Args:
            provider: Data source that supplies historical candles.
            strategy: Trading strategy that evaluates each candle.
            initial_capital: Starting capital in quote currency.

        """
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
        """Execute the backtest and return results.

        Fetch candles for the given symbol and interval within the time
        range, feed each candle to the strategy, process any resulting
        signals through the portfolio, and force-close any open position
        at the end.

        Args:
            symbol: Trading pair (e.g. ``BTC-USD``).
            interval: Candle time interval.
            start_ts: Start Unix timestamp in seconds.
            end_ts: End Unix timestamp in seconds.

        Returns:
            A ``BacktestResult`` with final capital, trades, and metrics.

        """
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
        """Return a zero-trade result when no candle data is available."""
        return BacktestResult(
            strategy_name=self._strategy.name,
            symbol=symbol,
            interval=interval,
            initial_capital=self._initial_capital,
            final_capital=self._initial_capital,
            trades=(),
        )
