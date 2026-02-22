"""Multi-asset backtest engine for simultaneous positions across symbols.

Run a single strategy against multiple symbols with interleaved
candles. Maintain per-symbol history and a shared portfolio that
can hold one position per symbol simultaneously.
"""

from decimal import Decimal

from trading_tools.apps.backtester.execution import check_risk_triggers
from trading_tools.apps.backtester.metrics import calculate_metrics
from trading_tools.apps.backtester.multi_asset_portfolio import MultiAssetPortfolio
from trading_tools.core.models import (
    ONE,
    BacktestResult,
    Candle,
    ExecutionConfig,
    Interval,
    RiskConfig,
    Side,
    Signal,
    Trade,
)
from trading_tools.core.protocols import CandleProvider, TradingStrategy


class MultiAssetEngine:
    """Run a strategy against multiple symbols with interleaved candles.

    Fetch candles for each symbol, merge them by timestamp, and feed
    each candle to the strategy with per-symbol history. The shared
    ``MultiAssetPortfolio`` allows one position per symbol
    simultaneously.
    """

    def __init__(
        self,
        provider: CandleProvider,
        strategy: TradingStrategy,
        symbols: list[str],
        initial_capital: Decimal,
        execution_config: ExecutionConfig | None = None,
        risk_config: RiskConfig | None = None,
    ) -> None:
        """Initialize the multi-asset backtest engine.

        Args:
            provider: Data source that supplies historical candles.
            strategy: Trading strategy evaluated on each candle.
            symbols: List of trading pair symbols to backtest.
            initial_capital: Starting capital in quote currency.
            execution_config: Optional execution cost configuration.
            risk_config: Optional risk-management configuration.

        """
        self._provider = provider
        self._strategy = strategy
        self._symbols = symbols
        self._initial_capital = initial_capital
        self._execution_config = execution_config
        self._risk_config = risk_config

    async def run(
        self,
        interval: Interval,
        start_ts: int,
        end_ts: int,
    ) -> BacktestResult:
        """Execute the multi-asset backtest and return results.

        Fetch candles for all symbols, merge by timestamp, run the
        strategy, force-close all positions at the end, and compute
        aggregate metrics.

        Args:
            interval: Candle time interval.
            start_ts: Start Unix timestamp in seconds.
            end_ts: End Unix timestamp in seconds.

        Returns:
            A ``BacktestResult`` with aggregate trades and metrics.

        """
        all_candles: list[Candle] = []
        for symbol in self._symbols:
            candles = await self._provider.get_candles(symbol, interval, start_ts, end_ts)
            all_candles.extend(candles)

        all_candles.sort(key=lambda c: c.timestamp)

        if not all_candles:
            return self._empty_result(interval)

        portfolio = MultiAssetPortfolio(
            self._initial_capital,
            self._execution_config,
            self._risk_config,
        )
        history: dict[str, list[Candle]] = {s: [] for s in self._symbols}
        latest_prices: dict[str, Decimal] = {}

        for candle in all_candles:
            symbol = candle.symbol
            if symbol not in history:
                history[symbol] = []

            latest_prices[symbol] = candle.close
            portfolio.update_equity(latest_prices)

            risk_trade = self._check_risk_exit(candle, portfolio)
            if risk_trade is None:
                symbol_history = history[symbol]
                signal = self._strategy.on_candle(candle, symbol_history)
                if signal is not None:
                    portfolio.process_signal(
                        signal,
                        candle.close,
                        candle.timestamp,
                        symbol_history,
                    )
            history[symbol].append(candle)

        last_prices: dict[str, Decimal] = {}
        for candle in reversed(all_candles):
            if candle.symbol not in last_prices:
                last_prices[candle.symbol] = candle.close
        last_ts = all_candles[-1].timestamp
        portfolio.force_close_all(last_prices, last_ts)

        trades = portfolio.trades
        metrics = calculate_metrics(trades, self._initial_capital, portfolio.capital)

        return BacktestResult(
            strategy_name=self._strategy.name,
            symbol=",".join(self._symbols),
            interval=interval,
            initial_capital=self._initial_capital,
            final_capital=portfolio.capital,
            trades=tuple(trades),
            metrics=metrics,
            candles=tuple(all_candles),
        )

    def _check_risk_exit(self, candle: Candle, portfolio: MultiAssetPortfolio) -> Trade | None:
        """Check and execute risk-management exits for the candle's symbol.

        Delegate trigger evaluation to the shared ``check_risk_triggers``
        helper. If triggered, send a SELL signal to close the position
        at the computed exit price.

        Args:
            candle: The current candle being processed.
            portfolio: The multi-asset portfolio to check.

        Returns:
            A ``Trade`` if a risk exit was triggered, ``None`` otherwise.

        """
        if self._risk_config is None:
            return None

        positions = portfolio.positions
        symbol = candle.symbol
        if symbol not in positions:
            return None

        position = positions[symbol]
        exit_price = check_risk_triggers(
            candle,
            position.entry_price,
            self._risk_config,
            side=position.side,
        )
        if exit_price is not None:
            reason = (
                "Stop-loss triggered"
                if (
                    (position.side == Side.BUY and exit_price < position.entry_price)
                    or (position.side == Side.SELL and exit_price > position.entry_price)
                )
                else "Take-profit triggered"
            )
            sell_signal = Signal(
                side=Side.SELL,
                symbol=symbol,
                strength=ONE,
                reason=reason,
            )
            return portfolio.process_signal(sell_signal, exit_price, candle.timestamp)

        return None

    def _empty_result(self, interval: Interval) -> BacktestResult:
        """Return a zero-trade result when no candle data is available."""
        return BacktestResult(
            strategy_name=self._strategy.name,
            symbol=",".join(self._symbols),
            interval=interval,
            initial_capital=self._initial_capital,
            final_capital=self._initial_capital,
            trades=(),
        )
