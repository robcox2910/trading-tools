"""Core data models shared across the trading tools application.

Define the immutable value objects (Candle, Signal, Trade, BacktestResult)
and mutable state (Position) that flow between the data providers,
strategies, portfolio tracker, and backtester engine.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum


class Side(Enum):
    """Direction of a trade: BUY (go long) or SELL (close / go short)."""

    BUY = "BUY"
    SELL = "SELL"


class Interval(Enum):
    """Supported candle time intervals from 1-minute to 1-week."""

    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"
    W1 = "1w"


@dataclass(frozen=True)
class Candle:
    """Immutable OHLCV candle representing one time period of market data.

    Each candle captures the open, high, low, and close prices plus
    the trading volume for a single interval (e.g. one hour) of a
    specific symbol.
    """

    symbol: str
    timestamp: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    interval: Interval


@dataclass(frozen=True)
class Signal:
    """Immutable trading signal emitted by a strategy.

    Carry the direction (BUY / SELL), target symbol, a confidence
    strength between 0 and 1, and a human-readable reason string
    explaining why the signal was generated.
    """

    side: Side
    symbol: str
    strength: Decimal
    reason: str

    def __post_init__(self) -> None:
        """Validate signal strength is between 0 and 1."""
        if not (Decimal(0) <= self.strength <= Decimal(1)):
            msg = f"strength must be between 0 and 1, got {self.strength}"
            raise ValueError(msg)


@dataclass(frozen=True)
class Trade:
    """Immutable record of a completed round-trip trade (entry + exit).

    Store the symbol, direction, quantity, entry/exit prices, and
    timestamps. Derived properties ``pnl`` and ``pnl_pct`` compute
    the absolute and percentage profit or loss.
    """

    symbol: str
    side: Side
    quantity: Decimal
    entry_price: Decimal
    entry_time: int
    exit_price: Decimal
    exit_time: int

    @property
    def pnl(self) -> Decimal:
        """Return the absolute profit or loss in quote currency."""
        if self.side == Side.SELL:
            return (self.entry_price - self.exit_price) * self.quantity
        return (self.exit_price - self.entry_price) * self.quantity

    @property
    def pnl_pct(self) -> Decimal:
        """Return the percentage gain or loss relative to the entry price."""
        if self.side == Side.SELL:
            return (self.entry_price - self.exit_price) / self.entry_price
        return (self.exit_price - self.entry_price) / self.entry_price


@dataclass
class Position:
    """Mutable representation of an open position awaiting an exit.

    Track the symbol, direction, quantity, entry price, and entry time.
    Call ``close()`` with an exit price and time to produce an immutable
    ``Trade`` record.
    """

    symbol: str
    side: Side
    quantity: Decimal
    entry_price: Decimal
    entry_time: int

    def close(self, exit_price: Decimal, exit_time: int) -> Trade:
        """Close this position at the given exit price and time and return a Trade."""
        return Trade(
            symbol=self.symbol,
            side=self.side,
            quantity=self.quantity,
            entry_price=self.entry_price,
            entry_time=self.entry_time,
            exit_price=exit_price,
            exit_time=exit_time,
        )


def _empty_metrics() -> dict[str, Decimal]:
    """Create an empty metrics dictionary."""
    return {}


@dataclass(frozen=True)
class BacktestResult:
    """Immutable summary of a completed backtest run.

    Bundle the strategy name, symbol, interval, capital figures,
    the full list of trades, and computed performance metrics into
    a single result object.
    """

    strategy_name: str
    symbol: str
    interval: Interval
    initial_capital: Decimal
    final_capital: Decimal
    trades: tuple[Trade, ...]
    metrics: dict[str, Decimal] = field(default_factory=_empty_metrics)
