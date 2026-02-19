"""Structural protocols for pluggable data sources and strategies."""

from typing import Protocol, runtime_checkable

from trading_tools.core.models import Candle, Interval, Signal


@runtime_checkable
class CandleProvider(Protocol):
    """Async provider of OHLCV candle data."""

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
    """Sync trading strategy that processes candles into signals."""

    @property
    def name(self) -> str:
        """Return the strategy name."""
        ...

    def on_candle(self, candle: Candle, history: list[Candle]) -> Signal | None:
        """Process a candle and return a signal or None."""
        ...
