"""Core data models shared across the trading tools application.

Define the immutable value objects (Candle, Signal, Trade, BacktestResult)
and mutable state (Position) that flow between the data providers,
strategies, portfolio tracker, and backtester engine.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum

ZERO = Decimal(0)
ONE = Decimal(1)


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
class ExecutionConfig:
    """Configure trade execution costs and position sizing.

    Control the realistic simulation of trade execution by specifying
    maker/taker fees, slippage, and the fraction of capital to deploy
    per trade. All percentages are expressed as decimals (e.g. 0.001
    for 0.1%). Default values preserve zero-cost, full-deployment
    behavior for backward compatibility.

    When ``volatility_sizing`` is enabled, position size is computed
    from ATR so that each trade risks approximately ``target_risk_pct``
    of total capital. The result is capped at ``position_size_pct``.
    """

    maker_fee_pct: Decimal = ZERO
    taker_fee_pct: Decimal = ZERO
    slippage_pct: Decimal = ZERO
    position_size_pct: Decimal = ONE
    volatility_sizing: bool = False
    atr_period: int = 14
    target_risk_pct: Decimal = Decimal("0.02")


@dataclass(frozen=True)
class RiskConfig:
    """Configure automatic risk-management exits.

    Define stop-loss and take-profit thresholds as decimal fractions
    of the entry price. When set, the backtest engine will close a
    position automatically if the candle's low breaches the stop-loss
    level or the candle's high breaches the take-profit level. ``None``
    disables the corresponding exit.

    Optionally enable a drawdown circuit breaker that halts new trades
    when portfolio equity drops by ``circuit_breaker_pct`` from peak.
    Trading resumes after equity recovers by ``recovery_pct`` from
    the halt point. Both must be set to enable the circuit breaker.
    """

    stop_loss_pct: Decimal | None = None
    take_profit_pct: Decimal | None = None
    circuit_breaker_pct: Decimal | None = None
    recovery_pct: Decimal | None = None


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
    entry_fee: Decimal = field(default=ZERO)
    exit_fee: Decimal = field(default=ZERO)

    @property
    def pnl(self) -> Decimal:
        """Return the absolute profit or loss in quote currency, net of fees."""
        if self.side == Side.SELL:
            raw = (self.entry_price - self.exit_price) * self.quantity
        else:
            raw = (self.exit_price - self.entry_price) * self.quantity
        return raw - self.entry_fee - self.exit_fee

    @property
    def pnl_pct(self) -> Decimal:
        """Return the percentage gain or loss relative to cost basis.

        Cost basis is the total entry value plus the entry fee. This
        gives a more realistic return percentage that accounts for
        transaction costs.
        """
        entry_value = self.entry_price * self.quantity
        cost_basis = entry_value + self.entry_fee
        if self.side == Side.SELL:
            raw = (self.entry_price - self.exit_price) * self.quantity
        else:
            raw = (self.exit_price - self.entry_price) * self.quantity
        net = raw - self.entry_fee - self.exit_fee
        return net / cost_basis


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

    def close(
        self,
        exit_price: Decimal,
        exit_time: int,
        entry_fee: Decimal = ZERO,
        exit_fee: Decimal = ZERO,
    ) -> Trade:
        """Close this position at the given exit price and time and return a Trade.

        Args:
            exit_price: Price at which the position is closed.
            exit_time: Unix timestamp of the exit.
            entry_fee: Fee paid when opening the position.
            exit_fee: Fee paid when closing the position.

        Returns:
            An immutable ``Trade`` recording the round-trip.

        """
        return Trade(
            symbol=self.symbol,
            side=self.side,
            quantity=self.quantity,
            entry_price=self.entry_price,
            entry_time=self.entry_time,
            exit_price=exit_price,
            exit_time=exit_time,
            entry_fee=entry_fee,
            exit_fee=exit_fee,
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
    candles: tuple[Candle, ...] = ()
