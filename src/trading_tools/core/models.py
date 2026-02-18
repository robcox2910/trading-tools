"""Core data models for the trading tools backtester."""

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum


class Side(Enum):
    """Trade direction."""

    BUY = "BUY"
    SELL = "SELL"


class Interval(Enum):
    """Candle time intervals."""

    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"
    W1 = "1w"


@dataclass(frozen=True)
class Candle:
    """OHLCV candle data."""

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
    """Trading signal emitted by a strategy."""

    side: Side
    symbol: str
    strength: Decimal
    reason: str

    def __post_init__(self) -> None:
        if not (Decimal("0") <= self.strength <= Decimal("1")):
            msg = f"strength must be between 0 and 1, got {self.strength}"
            raise ValueError(msg)


@dataclass(frozen=True)
class Trade:
    """A completed round-trip trade."""

    symbol: str
    side: Side
    quantity: Decimal
    entry_price: Decimal
    entry_time: int
    exit_price: Decimal
    exit_time: int

    @property
    def pnl(self) -> Decimal:
        """Absolute profit/loss."""
        return (self.exit_price - self.entry_price) * self.quantity

    @property
    def pnl_pct(self) -> Decimal:
        """Percentage return on entry."""
        return (self.exit_price - self.entry_price) / self.entry_price


@dataclass
class Position:
    """An open position that can be closed into a Trade."""

    symbol: str
    side: Side
    quantity: Decimal
    entry_price: Decimal
    entry_time: int

    def close(self, exit_price: Decimal, exit_time: int) -> Trade:
        """Close this position and return the resulting Trade."""
        return Trade(
            symbol=self.symbol,
            side=self.side,
            quantity=self.quantity,
            entry_price=self.entry_price,
            entry_time=self.entry_time,
            exit_price=exit_price,
            exit_time=exit_time,
        )


@dataclass(frozen=True)
class BacktestResult:
    """Results from a backtest run."""

    strategy_name: str
    symbol: str
    interval: Interval
    initial_capital: Decimal
    final_capital: Decimal
    trades: tuple[Trade, ...]
    metrics: dict[str, Decimal] = field(default_factory=lambda: dict[str, Decimal]())
