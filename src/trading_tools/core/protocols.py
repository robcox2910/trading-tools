"""Structural protocols for pluggable data sources and strategies.

Define the ``CandleProvider`` and ``TradingStrategy`` interfaces that
decouple the backtester engine from concrete implementations. Any class
whose shape matches these protocols can be used without explicit
inheritance (structural subtyping).
"""

from typing import Protocol, runtime_checkable

from trading_tools.core.models import Candle, Interval, Signal


@runtime_checkable
class CandleProvider(Protocol):
    """Async provider of OHLCV candle data.

    Implementors fetch candles from a specific data source (CSV file,
    Revolut X API, Binance API, etc.) and return them as a list of
    ``Candle`` objects sorted by timestamp.
    """

    async def get_candles(
        self,
        symbol: str,
        interval: Interval,
        start_ts: int,
        end_ts: int,
    ) -> list[Candle]:
        """Return candles for the given symbol, interval, and time range."""
        ...


@runtime_checkable
class TradingStrategy(Protocol):
    """Synchronous trading strategy that processes candles into signals.

    Implementors receive each candle one at a time along with the full
    history of previously seen candles. Return a ``Signal`` to indicate
    a trade action, or ``None`` to hold.
    """

    @property
    def name(self) -> str:
        """Return the strategy name."""
        ...

    def on_candle(self, candle: Candle, history: list[Candle]) -> Signal | None:
        """Process a candle and return a signal or None."""
        ...
