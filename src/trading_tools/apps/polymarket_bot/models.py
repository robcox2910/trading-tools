"""Data models for the Polymarket paper trading bot.

Define the immutable value objects that flow through the bot's pipeline:
market snapshots replace candles as the primary data unit, configuration
controls bot behaviour, paper trades record virtual executions, and the
result object captures a complete run's summary.
"""

from dataclasses import dataclass, field
from decimal import Decimal

from trading_tools.clients.polymarket.models import OrderBook
from trading_tools.core.models import ONE, ZERO, Side

_DEFAULT_INITIAL_CAPITAL = Decimal(1000)
_DEFAULT_MAX_POSITION_PCT = Decimal("0.1")
_DEFAULT_KELLY_FRACTION = Decimal("0.25")
_DEFAULT_ORDER_BOOK_REFRESH = 30
_DEFAULT_BALANCE_REFRESH = 60
_DEFAULT_SNIPE_POLL_INTERVAL = 1
_DEFAULT_SNIPE_WINDOW = 60
_DEFAULT_MAX_HISTORY = 500
_DEFAULT_MIN_EDGE = Decimal("0.005")
_DEFAULT_DRAWDOWN_ALERT_PCT = Decimal(-20)


@dataclass(frozen=True)
class MarketSnapshot:
    """Point-in-time snapshot of a prediction market.

    Replace candles as the primary data unit for prediction markets.
    Capture the current YES/NO prices, order book state, volume, and
    liquidity for a single market at a specific moment.

    Args:
        condition_id: Unique identifier for the market condition.
        question: The prediction question text.
        timestamp: Unix epoch seconds when the snapshot was taken.
        yes_price: Current YES token price (probability between 0 and 1).
        no_price: Current NO token price (probability between 0 and 1).
        order_book: Full order book snapshot with bids and asks.
        volume: Total trading volume in USD.
        liquidity: Current available liquidity in USD.
        end_date: ISO-8601 date string when the market resolves.

    Raises:
        ValueError: If prices are outside the valid [0, 1] range.

    """

    condition_id: str
    question: str
    timestamp: int
    yes_price: Decimal
    no_price: Decimal
    order_book: OrderBook
    volume: Decimal
    liquidity: Decimal
    end_date: str

    def __post_init__(self) -> None:
        """Validate that prices are within the valid probability range."""
        if not (ZERO <= self.yes_price <= ONE):
            msg = f"yes_price must be between 0 and 1, got {self.yes_price}"
            raise ValueError(msg)
        if not (ZERO <= self.no_price <= ONE):
            msg = f"no_price must be between 0 and 1, got {self.no_price}"
            raise ValueError(msg)


@dataclass(frozen=True)
class BotConfig:
    """Configuration for the trading bot (paper and live).

    Control WebSocket-driven event loop behaviour, background refresh
    intervals, capital allocation, Kelly sizing, and which markets to
    track. All monetary values use ``Decimal`` for precision.

    Args:
        order_book_refresh_seconds: Seconds between background HTTP order
            book refreshes (WebSocket only provides trade prices).
        balance_refresh_seconds: Seconds between balance refreshes from the
            CLOB API (live engine only).
        snipe_poll_seconds: Seconds between polls inside the snipe window.
        snipe_window_seconds: Seconds before market end that define the
            snipe window. Outside this window the engine sleeps; inside it
            the engine fast-polls at ``snipe_poll_seconds``.
        initial_capital: Starting virtual capital in USD.
        max_position_pct: Maximum fraction of capital per market (0-1).
        kelly_fraction: Fractional Kelly multiplier (e.g. 0.25 = quarter-Kelly).
        max_history: Maximum number of snapshots to retain per market.
        markets: Tuple of condition IDs to track.
        market_end_times: Pairs of (condition_id, ISO end time) for precise
            resolution time overrides (CLOB API only provides the date).
        min_edge: Minimum probability edge over market price required
            to open a position. Ensures the Kelly-estimated probability
            exceeds the buy price by at least this amount.
        series_slugs: Series slugs for periodic market re-discovery
            (e.g. ``("btc-updown-5m",)``). When set, the engine rotates
            markets each time the 5-minute window changes.

    """

    order_book_refresh_seconds: int = _DEFAULT_ORDER_BOOK_REFRESH
    balance_refresh_seconds: int = _DEFAULT_BALANCE_REFRESH
    snipe_poll_seconds: int = _DEFAULT_SNIPE_POLL_INTERVAL
    snipe_window_seconds: int = _DEFAULT_SNIPE_WINDOW
    initial_capital: Decimal = _DEFAULT_INITIAL_CAPITAL
    max_position_pct: Decimal = _DEFAULT_MAX_POSITION_PCT
    kelly_fraction: Decimal = _DEFAULT_KELLY_FRACTION
    max_history: int = _DEFAULT_MAX_HISTORY
    min_edge: Decimal = _DEFAULT_MIN_EDGE
    drawdown_alert_pct: Decimal = _DEFAULT_DRAWDOWN_ALERT_PCT
    markets: tuple[str, ...] = ()
    market_end_times: tuple[tuple[str, str], ...] = ()
    series_slugs: tuple[str, ...] = ()


@dataclass(frozen=True)
class PaperTrade:
    """Record of a virtual trade execution in the paper trading bot.

    Capture the full context of a simulated trade including the market,
    outcome token, direction, size, price, and the strategy's reasoning.

    Args:
        condition_id: Market condition identifier.
        token_outcome: Outcome token traded ("Yes" or "No").
        side: Trade direction (BUY or SELL).
        quantity: Number of tokens traded.
        price: Execution price between 0 and 1.
        timestamp: Unix epoch seconds of execution.
        reason: Human-readable explanation of why the trade was made.
        estimated_edge: Strategy's estimated probability edge over market price.

    """

    condition_id: str
    token_outcome: str
    side: Side
    quantity: Decimal
    price: Decimal
    timestamp: int
    reason: str
    estimated_edge: Decimal


def _empty_metrics() -> dict[str, Decimal]:
    """Create an empty metrics dictionary."""
    return {}


@dataclass(frozen=True)
class PaperTradingResult:
    """Summary of a completed paper trading bot run.

    Bundle the strategy name, capital figures, trade log, snapshot count,
    and computed performance metrics into a single immutable result.

    Args:
        strategy_name: Name of the strategy that was run.
        initial_capital: Starting virtual capital.
        final_capital: Ending virtual capital after all trades.
        trades: Tuple of all paper trades executed during the run.
        snapshots_processed: Total number of market snapshots processed.
        metrics: Performance metrics dictionary (total_return, win_rate, etc.).

    """

    strategy_name: str
    initial_capital: Decimal
    final_capital: Decimal
    trades: tuple[PaperTrade, ...]
    snapshots_processed: int
    metrics: dict[str, Decimal] = field(default_factory=_empty_metrics)


@dataclass(frozen=True)
class LiveTrade:
    """Record of a real trade execution on the Polymarket CLOB.

    Capture the full context of an executed trade including the market
    identifiers, CLOB order ID, fill information, and the strategy's
    reasoning. Unlike ``PaperTrade``, this includes ``token_id`` and
    ``order_id`` from the live CLOB response.

    Args:
        condition_id: Market condition identifier.
        token_id: CLOB token identifier that was traded.
        token_outcome: Outcome token traded ("Yes" or "No").
        order_id: Unique order identifier assigned by the CLOB.
        side: Trade direction (BUY or SELL).
        quantity: Number of tokens submitted.
        price: Submitted price between 0 and 1.
        filled: Actual number of tokens filled by the CLOB.
        timestamp: Unix epoch seconds of execution.
        reason: Human-readable explanation of why the trade was made.
        estimated_edge: Strategy's estimated probability edge over market price.

    """

    condition_id: str
    token_id: str
    token_outcome: str
    order_id: str
    side: Side
    quantity: Decimal
    price: Decimal
    filled: Decimal
    timestamp: int
    reason: str
    estimated_edge: Decimal


@dataclass(frozen=True)
class LiveTradingResult:
    """Summary of a completed live trading bot run.

    Bundle the strategy name, balance figures, trade log, snapshot count,
    and computed performance metrics into a single immutable result.

    Args:
        strategy_name: Name of the strategy that was run.
        initial_balance: Starting USDC balance from the CLOB API.
        final_balance: Ending USDC balance after all trades.
        trades: Tuple of all live trades executed during the run.
        snapshots_processed: Total number of market snapshots processed.
        metrics: Performance metrics dictionary (total_return, win_rate, etc.).

    """

    strategy_name: str
    initial_balance: Decimal
    final_balance: Decimal
    trades: tuple[LiveTrade, ...]
    snapshots_processed: int
    metrics: dict[str, Decimal] = field(default_factory=_empty_metrics)
