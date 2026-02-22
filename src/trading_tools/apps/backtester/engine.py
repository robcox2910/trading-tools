"""Backtest engine that orchestrates strategy evaluation against candle data.

Fetch candles from a ``CandleProvider``, feed them one-by-one to a
``TradingStrategy``, and track the resulting trades and portfolio state.
Optionally apply execution costs (fees, slippage, position sizing) and
risk-management exits (stop-loss, take-profit). Return a
``BacktestResult`` containing final capital, trade history, and
performance metrics.
"""

from decimal import Decimal

from trading_tools.apps.backtester.execution import check_risk_triggers
from trading_tools.apps.backtester.metrics import calculate_metrics
from trading_tools.apps.backtester.portfolio import Portfolio
from trading_tools.core.models import (
    BacktestResult,
    Candle,
    ExecutionConfig,
    Interval,
    RiskConfig,
    Trade,
)
from trading_tools.core.protocols import CandleProvider, TradingStrategy


class BacktestEngine:
    """Run a trading strategy against historical candle data.

    Coordinate a ``CandleProvider`` (data source), ``TradingStrategy``
    (signal generation), and ``Portfolio`` (position tracking) to simulate
    trading over a historical period. Optionally apply execution costs
    and risk-management exits.
    """

    def __init__(
        self,
        provider: CandleProvider,
        strategy: TradingStrategy,
        initial_capital: Decimal,
        execution_config: ExecutionConfig | None = None,
        risk_config: RiskConfig | None = None,
    ) -> None:
        """Initialize the backtest engine.

        Args:
            provider: Data source that supplies historical candles.
            strategy: Trading strategy that evaluates each candle.
            initial_capital: Starting capital in quote currency.
            execution_config: Optional execution cost configuration
                (fees, slippage, position sizing).
            risk_config: Optional risk-management configuration
                (stop-loss, take-profit thresholds).

        """
        self._provider = provider
        self._strategy = strategy
        self._initial_capital = initial_capital
        self._execution_config = execution_config
        self._risk_config = risk_config

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

        portfolio = Portfolio(
            self._initial_capital,
            self._execution_config,
            self._risk_config,
        )
        history: list[Candle] = []

        for candle in candles:
            portfolio.update_equity(candle.close)
            risk_trade = self._check_risk_exit(candle, portfolio)
            if risk_trade is None:
                signal = self._strategy.on_candle(candle, history)
                if signal is not None:
                    portfolio.process_signal(
                        signal,
                        candle.close,
                        candle.timestamp,
                        history,
                    )
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
            candles=tuple(candles),
        )

    def _check_risk_exit(self, candle: Candle, portfolio: Portfolio) -> Trade | None:
        """Check whether a risk-management exit is triggered on this candle.

        Delegate trigger evaluation to the shared ``check_risk_triggers``
        helper. If triggered, force-close the portfolio position at the
        computed exit price.

        Args:
            candle: The current candle being processed.
            portfolio: The portfolio to check for open positions.

        Returns:
            A ``Trade`` if a risk exit was triggered, ``None`` otherwise.

        """
        if self._risk_config is None or portfolio.position is None:
            return None

        exit_price = check_risk_triggers(
            candle,
            portfolio.position.entry_price,
            self._risk_config,
            side=portfolio.position.side,
        )
        if exit_price is not None:
            return portfolio.force_close(exit_price, candle.timestamp)

        return None

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
