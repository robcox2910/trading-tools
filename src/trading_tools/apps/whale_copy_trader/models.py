"""Data models for the whale copy-trading service.

Define value objects for copy signals (detected whale bias), side legs
(individual Up/Down token positions), open positions that transition
through UNHEDGED → HEDGED states for temporal spread arbitrage, and
closed trade results.

Also define the ``CopyResultRecord`` SQLAlchemy ORM model for persisting
closed trade results to PostgreSQL (or SQLite) for post-hoc analysis
and backtesting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum

from sqlalchemy import BigInteger, Boolean, Float, Index, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class PositionState(Enum):
    """Lifecycle state of a copy-trading position.

    UNHEDGED: Leg 1 (directional) placed, waiting for hedge opportunity.
    HEDGED: Both legs placed, guaranteed profit locked in.
    STOPPED: Exited via stop-loss (unhedged, price dropped too far).
    EXITED: Exited via take-profit (unhedged, price rose high enough to sell).
    """

    UNHEDGED = "unhedged"
    HEDGED = "hedged"
    STOPPED = "stopped"
    EXITED = "exited"


@dataclass(frozen=True)
class CopySignal:
    """A detected whale directional bias worth copying.

    Represent a market where the whale has taken a strong enough position
    (above the bias threshold) on a parseable BTC/ETH Up/Down market
    whose time window has not yet expired.

    Attributes:
        condition_id: Polymarket market condition identifier.
        title: Human-readable market title.
        asset: Spot trading pair (``"BTC-USD"`` or ``"ETH-USD"``).
        favoured_side: Whale's favoured direction (``"Up"`` or ``"Down"``).
        bias_ratio: Volume ratio of favoured side to unfavoured.
        trade_count: Number of whale trades in this market.
        window_start_ts: UTC epoch seconds when the market window opens.
        window_end_ts: UTC epoch seconds when the market window closes.
        detected_at: UTC epoch seconds when the signal was detected.
        up_volume_pct: Fraction of whale spend on Up (0.0-1.0).
        down_volume_pct: Fraction of whale spend on Down (sums to 1.0).
        strength_score: Composite signal quality score (0.0-1.0) derived
            from bias ratio and trade count. Used for proportional sizing.

    """

    condition_id: str
    title: str
    asset: str
    favoured_side: str
    bias_ratio: Decimal
    trade_count: int
    window_start_ts: int
    window_end_ts: int
    detected_at: int
    up_volume_pct: Decimal = Decimal("0.5")
    down_volume_pct: Decimal = Decimal("0.5")
    strength_score: Decimal = Decimal("1.0")


@dataclass
class FlipState:
    """Track per-market flip activity across close/reopen cycles.

    Maintain a running count of flips executed within a single market
    window so the trader can enforce ``max_flips_per_market`` and use
    the original signal's metadata for logging. The state persists
    across position close/reopen cycles because each flip closes one
    position and immediately opens another.

    Attributes:
        original_signal: The initial copy signal that triggered entry.
        flip_count: Number of flips executed so far in this market.
        last_flip_side: The side of the most recent flip entry
            (``"Up"`` or ``"Down"``).
        entry_amount: Dollar amount used for each flip (fixed from
            the first entry's cost basis).

    """

    original_signal: CopySignal
    flip_count: int = 0
    last_flip_side: str = ""
    entry_amount: Decimal = Decimal(0)


def _empty_str_list() -> list[str]:
    """Return an empty list[str] for dataclass default_factory."""
    return []


@dataclass
class SideLeg:
    """One side of a spread position (Up or Down).

    Track the weighted-average entry price, total quantity, and cost basis
    for a single outcome token. Mutable so fills can be added incrementally.

    Attributes:
        side: Outcome direction (``"Up"`` or ``"Down"``).
        entry_price: Weighted-average entry price across all fills.
        quantity: Total token quantity across all fills.
        cost_basis: Total USDC spent (sum of price * quantity per fill).
        order_ids: CLOB order IDs for live trades on this leg.

    """

    side: str
    entry_price: Decimal
    quantity: Decimal
    cost_basis: Decimal
    order_ids: list[str] = field(default_factory=_empty_str_list)

    def add_fill(self, price: Decimal, qty: Decimal) -> None:
        """Add a new fill and update the weighted-average entry price.

        Args:
            price: Entry price of the new fill.
            qty: Quantity of the new fill.

        """
        new_cost = price * qty
        self.cost_basis += new_cost
        self.quantity += qty
        self.entry_price = (self.cost_basis / self.quantity).quantize(Decimal("0.0001"))


@dataclass
class OpenPosition:
    """A live (open) temporal spread arbitrage position.

    Start with a single directional leg (leg1) copying the whale's
    favoured side. Transition to HEDGED when the opposite side becomes
    cheap enough that combined cost < ``max_spread_cost``, locking in
    guaranteed profit.

    Attributes:
        signal: The most recent signal for this market.
        state: Current lifecycle state (UNHEDGED or HEDGED).
        leg1: The directional entry leg (whale's favoured side).
        hedge_leg: The hedge leg (opposite side), or ``None`` if unhedged.
        hedge_side: The outcome name for the hedge leg (opposite of leg1).
        entry_time: UTC epoch seconds of the first entry.
        is_paper: ``True`` for simulated trades, ``False`` for live.

    """

    signal: CopySignal
    state: PositionState
    leg1: SideLeg
    hedge_leg: SideLeg | None
    hedge_side: str
    entry_time: int
    is_paper: bool = True

    @property
    def total_cost_basis(self) -> Decimal:
        """Return the combined cost basis of both legs."""
        hedge_cost = self.hedge_leg.cost_basis if self.hedge_leg else Decimal(0)
        return self.leg1.cost_basis + hedge_cost

    @property
    def all_order_ids(self) -> list[str]:
        """Return all CLOB order IDs across both legs."""
        ids: list[str] = list(self.leg1.order_ids)
        if self.hedge_leg:
            ids.extend(self.hedge_leg.order_ids)
        return ids

    @property
    def favoured_side(self) -> str:
        """Return the whale's favoured side (leg1 direction)."""
        return self.leg1.side


@dataclass(frozen=True)
class CopyResult:
    """Outcome of a closed copy trade.

    Immutable record of a position that has been closed, with final
    P&L calculated from the winning leg's payout minus total cost.

    Attributes:
        signal: The copy signal that triggered this trade.
        state: Position state at close (UNHEDGED or HEDGED).
        leg1_side: Direction of the directional entry leg.
        leg1_entry: Entry price for leg 1.
        leg1_qty: Token quantity for leg 1.
        hedge_entry: Entry price for hedge leg, or ``None``.
        hedge_qty: Token quantity for hedge leg, or ``None``.
        total_cost_basis: Total USDC spent across both legs.
        entry_time: UTC epoch seconds when first opened.
        exit_time: UTC epoch seconds when closed, or ``None`` if still open.
        winning_side: Which side won (``"Up"`` or ``"Down"``), or ``None``.
        pnl: Realised profit/loss in USDC.
        is_paper: ``True`` for simulated trades, ``False`` for live.
        order_ids: All CLOB order IDs for this position.
        outcome_known: ``True`` when the market outcome was definitively
            resolved. ``False`` when candle data was unavailable and a
            fallback P&L was applied (hedged → zero, unhedged → loss).

    """

    signal: CopySignal
    state: PositionState
    leg1_side: str
    leg1_entry: Decimal
    leg1_qty: Decimal
    hedge_entry: Decimal | None
    hedge_qty: Decimal | None
    total_cost_basis: Decimal
    entry_time: int
    exit_time: int | None = None
    winning_side: str | None = None
    pnl: Decimal = Decimal(0)
    is_paper: bool = True
    order_ids: tuple[str, ...] = ()
    outcome_known: bool = True
    flip_number: int | None = None


class Base(DeclarativeBase):
    """Declarative base class for all whale copy-trader ORM models."""


class CopyResultRecord(Base):
    """Persisted record of a closed copy trade for post-hoc analysis.

    Denormalize signal fields (condition_id, asset, favoured_side, etc.)
    alongside execution details so that queries can filter and aggregate
    without needing to join against a separate signals table. Store prices
    and P&L as floats for lightweight storage; epoch seconds as BigInteger
    for time-range queries.

    Attributes:
        id: Auto-incrementing primary key.
        condition_id: Polymarket market condition identifier.
        asset: Spot trading pair (``"BTC-USD"`` or ``"ETH-USD"``).
        favoured_side: Whale's favoured direction (``"Up"`` or ``"Down"``).
        bias_ratio: Volume ratio of favoured side to unfavoured.
        window_start_ts: UTC epoch seconds when the market window opens.
        window_end_ts: UTC epoch seconds when the market window closes.
        detected_at: UTC epoch seconds when the signal was first detected.
        state: Position state at close (e.g. ``"hedged"``, ``"unhedged"``).
        leg1_side: Direction of the directional entry leg.
        leg1_entry: Entry price for leg 1.
        leg1_qty: Token quantity for leg 1.
        hedge_entry: Entry price for hedge leg, or ``None``.
        hedge_qty: Token quantity for hedge leg, or ``None``.
        hedge_side: Outcome name for the hedge leg (opposite of leg1).
        total_cost_basis: Total USDC spent across both legs.
        entry_time: UTC epoch seconds when the position was first opened.
        exit_time: UTC epoch seconds when the position was closed.
        winning_side: Which side won (``"Up"`` or ``"Down"``), or ``None``.
        pnl: Realised profit/loss in USDC.
        is_paper: ``True`` for simulated trades, ``False`` for live.
        order_ids: Comma-separated CLOB order IDs for this position.
        outcome_known: ``True`` when the market outcome was definitively
            resolved via candle data. ``False`` when a fallback was used.

    """

    __tablename__ = "copy_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    condition_id: Mapped[str] = mapped_column(String, index=True)
    asset: Mapped[str] = mapped_column(String)
    favoured_side: Mapped[str] = mapped_column(String)
    bias_ratio: Mapped[float] = mapped_column(Float)
    window_start_ts: Mapped[int] = mapped_column(BigInteger)
    window_end_ts: Mapped[int] = mapped_column(BigInteger)
    detected_at: Mapped[int] = mapped_column(BigInteger)
    state: Mapped[str] = mapped_column(String)
    leg1_side: Mapped[str] = mapped_column(String)
    leg1_entry: Mapped[float] = mapped_column(Float)
    leg1_qty: Mapped[float] = mapped_column(Float)
    hedge_entry: Mapped[float | None] = mapped_column(Float, nullable=True)
    hedge_qty: Mapped[float | None] = mapped_column(Float, nullable=True)
    hedge_side: Mapped[str] = mapped_column(String)
    total_cost_basis: Mapped[float] = mapped_column(Float)
    entry_time: Mapped[int] = mapped_column(BigInteger)
    exit_time: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    winning_side: Mapped[str | None] = mapped_column(String, nullable=True)
    pnl: Mapped[float] = mapped_column(Float)
    is_paper: Mapped[bool] = mapped_column(Boolean)
    order_ids: Mapped[str] = mapped_column(String, default="")
    outcome_known: Mapped[bool] = mapped_column(Boolean, default=True)

    __table_args__ = (
        Index("ix_copy_results_condition_exit", "condition_id", "exit_time"),
        Index("ix_copy_results_asset_exit", "asset", "exit_time"),
        Index("ix_copy_results_paper_exit", "is_paper", "exit_time"),
    )

    @classmethod
    def from_copy_result(cls, result: CopyResult) -> CopyResultRecord:
        """Create a database record from an in-memory ``CopyResult``.

        Flatten the nested ``CopySignal`` fields and convert ``Decimal``
        values to ``float`` for storage.

        Args:
            result: The closed trade result to persist.

        Returns:
            A new ``CopyResultRecord`` ready for insertion.

        """
        sig = result.signal
        return cls(
            condition_id=sig.condition_id,
            asset=sig.asset,
            favoured_side=sig.favoured_side,
            bias_ratio=float(sig.bias_ratio),
            window_start_ts=sig.window_start_ts,
            window_end_ts=sig.window_end_ts,
            detected_at=sig.detected_at,
            state=result.state.value,
            leg1_side=result.leg1_side,
            leg1_entry=float(result.leg1_entry),
            leg1_qty=float(result.leg1_qty),
            hedge_entry=float(result.hedge_entry) if result.hedge_entry is not None else None,
            hedge_qty=float(result.hedge_qty) if result.hedge_qty is not None else None,
            hedge_side="Down" if result.leg1_side == "Up" else "Up",
            total_cost_basis=float(result.total_cost_basis),
            entry_time=result.entry_time,
            exit_time=result.exit_time,
            winning_side=result.winning_side,
            pnl=float(result.pnl),
            is_paper=result.is_paper,
            order_ids=",".join(result.order_ids),
            outcome_known=result.outcome_known,
        )
