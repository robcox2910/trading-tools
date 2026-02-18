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
    ) -> list[Candle]: ...


@runtime_checkable
class TradingStrategy(Protocol):
    """Sync trading strategy that processes candles into signals."""

    @property
    def name(self) -> str: ...

    def on_candle(self, candle: Candle, history: list[Candle]) -> Signal | None: ...
