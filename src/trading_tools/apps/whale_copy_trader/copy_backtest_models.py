"""Data models for the whale copy-trading backtest.

Define immutable value objects representing a single copy attempt at a given
time offset after the whale's trade, a complete per-trade record bundling the
whale's execution with all offset simulations, per-offset aggregate statistics,
and the top-level backtest result.

Sizing model
------------
Rather than copying a fixed percentage of the whale's token quantity, the
backtest uses a capital-proportional model that mirrors how a real copy trader
would allocate:

1. Compute the whale's trade cost in USD: ``whale_size * whale_price``.
2. Express that as a fraction of the whale's total wallet:
   ``whale_fraction = whale_trade_cost / whale_capital``.
3. Apply the same fraction to our allocated capital:
   ``our_budget_usd = whale_fraction * our_capital``.
4. Use ``our_budget_usd`` to walk the order book, buying as many tokens as the
   budget allows.

Example: whale spends $100 of a $10 000 wallet (1%).  We have $500 allocated
→ we deploy $5.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class CopyAttempt:
    """Simulated copy execution at a single time offset after the whale's trade.

    Record what order book was available at ``whale_ts_ms + offset_ms`` and
    how much of our USD budget could be deployed, at what VWAP token price.

    Attributes:
        offset_ms: Milliseconds after the whale trade that this attempt targets.
        snapshot_ts_ms: Actual timestamp of the nearest order book snapshot
            found, or 0 if none was found within tolerance.
        ob_found: Whether a usable order book snapshot was found.
        our_budget_usd: USD amount allocated to this copy
            (``whale_fraction * our_capital``).
        fill_qty: Token quantity bought by walking the order book.
        fill_price: VWAP execution price, or ``Decimal(0)`` if nothing filled.
        cost_usd: Actual USD spent (``fill_qty * fill_price``).
        slippage: ``fill_price - whale_price`` for a BUY copy, or
            ``whale_price - fill_price`` for a SELL copy. Positive means we got
            a worse price than the whale; negative means we got a better price.

    """

    offset_ms: int
    snapshot_ts_ms: int
    ob_found: bool
    our_budget_usd: Decimal
    fill_qty: Decimal
    fill_price: Decimal
    cost_usd: Decimal
    slippage: Decimal

    @property
    def budget_utilisation(self) -> Decimal:
        """Return fraction of budget spent (0-1).  Zero when budget is zero."""
        if self.our_budget_usd == Decimal(0):
            return Decimal(0)
        return (self.cost_usd / self.our_budget_usd).quantize(Decimal("0.0001"))

    @property
    def fully_deployed(self) -> bool:
        """Return True if at least 99% of budget was spent."""
        return self.budget_utilisation >= Decimal("0.99")


@dataclass(frozen=True)
class CopyBacktestTrade:
    """One whale trade with simulated copy attempts at each time offset.

    Bundle the original whale trade details alongside the vector of
    ``CopyAttempt`` objects (one per configured offset) so callers can
    compare execution quality across offsets for every trade event.

    Attributes:
        whale_address: Proxy wallet address of the whale.
        condition_id: Polymarket condition identifier.
        asset_id: CLOB token identifier (maps to order book snapshots).
        title: Human-readable market title.
        outcome: Outcome token label (e.g. ``"Up"``, ``"Down"``).
        side: Whale's trade direction — ``"BUY"`` or ``"SELL"``.
        whale_size: Raw token quantity traded by the whale.
        whale_price: Whale's execution price.
        whale_ts: Epoch seconds of the whale's trade.
        whale_trade_cost: USD value of the whale's trade
            (``whale_size * whale_price``).
        whale_fraction: Whale's trade as a fraction of their capital
            (``whale_trade_cost / whale_capital``).
        our_budget_usd: USD amount we allocated to copy this trade
            (``whale_fraction * our_capital``).
        attempts: One ``CopyAttempt`` per configured offset, in offset order.

    """

    whale_address: str
    condition_id: str
    asset_id: str
    title: str
    outcome: str
    side: str
    whale_size: Decimal
    whale_price: Decimal
    whale_ts: int
    whale_trade_cost: Decimal
    whale_fraction: Decimal
    our_budget_usd: Decimal
    attempts: tuple[CopyAttempt, ...]


@dataclass(frozen=True)
class OffsetStats:
    """Aggregate copy execution statistics for a single time offset.

    Summarise budget deployment and slippage across all trades at one offset
    value so callers can compare the cost of reacting at 500 ms vs 1 000 ms
    vs 5 000 ms after the whale.

    Attributes:
        offset_ms: The time offset these statistics apply to.
        trades_total: Total number of whale trades in the backtest.
        trades_with_book: Trades where an order book snapshot was found.
        trades_fully_deployed: Trades where 99%+ of budget was spent.
        trades_partial: Trades with partial budget deployment (0-99%).
        avg_budget_utilisation: Mean fraction of budget spent (0-1).
        avg_slippage: Mean slippage across trades with any fill.
        max_slippage: Worst (highest) slippage observed.
        avg_slippage_bps: Mean slippage in basis points.
        total_deployed_usd: Total USD deployed across all fills.

    """

    offset_ms: int
    trades_total: int
    trades_with_book: int
    trades_fully_deployed: int
    trades_partial: int
    avg_budget_utilisation: Decimal
    avg_slippage: Decimal
    max_slippage: Decimal
    avg_slippage_bps: Decimal
    total_deployed_usd: Decimal


@dataclass(frozen=True)
class CopyBacktestResult:
    """Top-level result from a copy-trading backtest run.

    Attributes:
        whale_address: Proxy wallet address that was monitored.
        whale_capital: Estimated total capital of the whale's wallet (USD).
        our_capital: Capital we allocated to copying this whale (USD).
        total_trades: Number of whale trades analysed.
        offsets_ms: Tuple of time offsets that were simulated.
        trades: Full per-trade records with all offset attempts.
        stats_by_offset: Per-offset aggregate statistics keyed by offset_ms.

    """

    whale_address: str
    whale_capital: Decimal
    our_capital: Decimal
    total_trades: int
    offsets_ms: tuple[int, ...]
    trades: tuple[CopyBacktestTrade, ...]
    stats_by_offset: dict[int, OffsetStats]
