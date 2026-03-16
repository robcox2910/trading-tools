"""Data models for the spread capture bot.

Define value objects for spread opportunities (markets where combined
price < threshold), paired positions (both sides bought), and closed
trade results.  Define ``SideLeg`` for
individual token positions.

Also define the ``SpreadResultRecord`` SQLAlchemy ORM model for
persisting closed trade results to PostgreSQL (or SQLite).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from sqlalchemy import BigInteger, Boolean, Float, Index, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class PositionState(Enum):
    """Lifecycle state of a spread capture position.

    PENDING: Orders placed, waiting for both sides to fill (live only).
    PAIRED: Both sides filled, guaranteed profit locked in.
    SINGLE_LEG: Only one side filled after timeout.
    SETTLED: Position closed at market expiry, P&L realised.
    """

    PENDING = "pending"
    PAIRED = "paired"
    SINGLE_LEG = "single_leg"
    SETTLED = "settled"


@dataclass(frozen=True)
class SpreadOpportunity:
    """A market where buying both sides costs less than $1.00.

    Represent an actionable spread opportunity discovered by the market
    scanner.  The ``margin`` field is the guaranteed profit per token
    pair at settlement (``1.0 - combined``).

    Attributes:
        condition_id: Polymarket market condition identifier.
        title: Human-readable market title.
        asset: Spot trading pair (e.g. ``"BTC-USD"``).
        up_token_id: CLOB token identifier for the Up outcome.
        down_token_id: CLOB token identifier for the Down outcome.
        up_price: Current CLOB price for the Up side.
        down_price: Current CLOB price for the Down side.
        combined: Sum of up_price and down_price.
        margin: Guaranteed profit per token pair (``1.0 - combined``).
        window_start_ts: UTC epoch seconds when the market window opens.
        window_end_ts: UTC epoch seconds when the market window closes.
        up_bid_depth: Best bid size (tokens) for Up outcome order book.
        down_bid_depth: Best bid size (tokens) for Down outcome order book.

    """

    condition_id: str
    title: str
    asset: str
    up_token_id: str
    down_token_id: str
    up_price: Decimal
    down_price: Decimal
    combined: Decimal
    margin: Decimal
    window_start_ts: int
    window_end_ts: int
    up_bid_depth: Decimal = Decimal(0)
    down_bid_depth: Decimal = Decimal(0)

    @property
    def fill_score(self) -> Decimal:
        """Return depth-weighted score that favours liquid, fillable markets.

        Use ``log2(1 + margin_bps)`` to compress the margin range so that
        a 30x margin advantage (e.g. HYPE 94% vs BTC 3%) becomes only ~3x
        in log-space.  Multiply by average bid depth so that deep order
        books dominate over thin ones.

        Example scores (real data):
        - BTC: margin=3%, depth=(96,403) → log2(301)*249 ≈ 2,070
        - HYPE: margin=94%, depth=(50,50) → log2(9401)*50 ≈ 649
        """
        margin_bps = float(self.margin) * _MARGIN_BPS_SCALE
        log_margin = Decimal(str(math.log2(1 + margin_bps)))
        avg_depth = (self.up_bid_depth + self.down_bid_depth) / _TWO
        return log_margin * avg_depth


_MARGIN_BPS_SCALE = 10_000
_TWO = Decimal(2)


class SideLeg:
    """One side of a spread position (Up or Down).

    Track the weighted-average entry price, total quantity, and cost basis
    for a single outcome token.  Mutable so fills can be added incrementally.

    Attributes:
        side: Outcome direction (``"Up"`` or ``"Down"``).
        entry_price: Weighted-average entry price across all fills.
        quantity: Total token quantity across all fills.
        cost_basis: Total USDC spent (sum of price * quantity per fill).
        order_ids: CLOB order IDs for live trades on this leg.

    """

    __slots__ = ("cost_basis", "entry_price", "order_ids", "quantity", "side")

    def __init__(
        self,
        side: str,
        entry_price: Decimal,
        quantity: Decimal,
        cost_basis: Decimal,
        order_ids: list[str] | None = None,
    ) -> None:
        """Initialize a side leg with entry details.

        Args:
            side: Outcome direction (``"Up"`` or ``"Down"``).
            entry_price: Entry price per token.
            quantity: Number of tokens.
            cost_basis: Total USDC spent.
            order_ids: Optional list of CLOB order IDs.

        """
        self.side = side
        self.entry_price = entry_price
        self.quantity = quantity
        self.cost_basis = cost_basis
        self.order_ids: list[str] = order_ids if order_ids is not None else []

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
class PairedPosition:
    """A live spread position with up to two sides.

    Track both the Up and Down legs of a spread trade, along with the
    opportunity that triggered entry and the current lifecycle state.

    Attributes:
        opportunity: The spread opportunity that triggered this position.
        state: Current lifecycle state.
        up_leg: The Up side leg.
        down_leg: The Down side leg, or ``None`` if not yet filled.
        entry_time: UTC epoch seconds when the position was opened.
        is_paper: ``True`` for simulated trades, ``False`` for live.
        pending_up_order_id: CLOB order ID for unfilled Up GTC order.
        pending_down_order_id: CLOB order ID for unfilled Down GTC order.

    """

    opportunity: SpreadOpportunity
    state: PositionState
    up_leg: SideLeg
    down_leg: SideLeg | None
    entry_time: int
    is_paper: bool = True
    pending_up_order_id: str | None = None
    pending_down_order_id: str | None = None

    @property
    def total_cost_basis(self) -> Decimal:
        """Return the combined cost basis of both legs."""
        down_cost = self.down_leg.cost_basis if self.down_leg else Decimal(0)
        return self.up_leg.cost_basis + down_cost

    @property
    def all_order_ids(self) -> list[str]:
        """Return all CLOB order IDs across both legs."""
        ids: list[str] = list(self.up_leg.order_ids)
        if self.down_leg:
            ids.extend(self.down_leg.order_ids)
        return ids

    @property
    def is_paired(self) -> bool:
        """Return ``True`` when both legs are filled."""
        return self.down_leg is not None and self.state == PositionState.PAIRED


@dataclass(frozen=True)
class SpreadResult:
    """Outcome of a closed spread trade.

    Immutable record of a position that has been settled, with final
    P&L calculated from the winning leg's payout minus total cost.

    Attributes:
        opportunity: The spread opportunity that triggered this trade.
        state: Position state at close.
        up_entry: Entry price for the Up leg.
        up_qty: Token quantity for the Up leg.
        down_entry: Entry price for the Down leg, or ``None``.
        down_qty: Token quantity for the Down leg, or ``None``.
        total_cost_basis: Total USDC spent across both legs.
        entry_time: UTC epoch seconds when first opened.
        exit_time: UTC epoch seconds when settled.
        winning_side: Which side won (``"Up"`` or ``"Down"``), or ``None``.
        pnl: Realised profit/loss in USDC.
        is_paper: ``True`` for simulated trades, ``False`` for live.
        order_ids: All CLOB order IDs for this position.
        outcome_known: ``True`` when the market outcome was definitively
            resolved via candle data.

    """

    opportunity: SpreadOpportunity
    state: PositionState
    up_entry: Decimal
    up_qty: Decimal
    down_entry: Decimal | None
    down_qty: Decimal | None
    total_cost_basis: Decimal
    entry_time: int
    exit_time: int
    winning_side: str | None = None
    pnl: Decimal = Decimal(0)
    is_paper: bool = True
    order_ids: tuple[str, ...] = ()
    outcome_known: bool = True


class SpreadBase(DeclarativeBase):
    """Declarative base class for all spread capture ORM models."""


class SpreadResultRecord(SpreadBase):
    """Persisted record of a closed spread trade for post-hoc analysis.

    Denormalize opportunity fields alongside execution details so that
    queries can filter and aggregate without needing to join against a
    separate opportunities table.

    Attributes:
        id: Auto-incrementing primary key.
        condition_id: Polymarket market condition identifier.
        asset: Spot trading pair (e.g. ``"BTC-USD"``).
        window_start_ts: UTC epoch when the market window opens.
        window_end_ts: UTC epoch when the market window closes.
        state: Position state at close.
        up_entry: Entry price for the Up leg.
        up_qty: Token quantity for the Up leg.
        down_entry: Entry price for the Down leg, or ``None``.
        down_qty: Token quantity for the Down leg, or ``None``.
        total_cost_basis: Total USDC spent across both legs.
        combined_price: Sum of Up and Down entry prices.
        margin: Guaranteed profit per token at entry.
        entry_time: UTC epoch seconds when opened.
        exit_time: UTC epoch seconds when settled.
        winning_side: Which side won, or ``None``.
        pnl: Realised profit/loss in USDC.
        is_paper: Paper or live trade.
        order_ids: Comma-separated CLOB order IDs.
        outcome_known: Whether the outcome was resolved via candle data.

    """

    __tablename__ = "spread_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    condition_id: Mapped[str] = mapped_column(String, index=True)
    asset: Mapped[str] = mapped_column(String)
    window_start_ts: Mapped[int] = mapped_column(BigInteger)
    window_end_ts: Mapped[int] = mapped_column(BigInteger)
    state: Mapped[str] = mapped_column(String)
    up_entry: Mapped[float] = mapped_column(Float)
    up_qty: Mapped[float] = mapped_column(Float)
    down_entry: Mapped[float | None] = mapped_column(Float, nullable=True)
    down_qty: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_cost_basis: Mapped[float] = mapped_column(Float)
    combined_price: Mapped[float] = mapped_column(Float)
    margin: Mapped[float] = mapped_column(Float)
    entry_time: Mapped[int] = mapped_column(BigInteger)
    exit_time: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    winning_side: Mapped[str | None] = mapped_column(String, nullable=True)
    pnl: Mapped[float] = mapped_column(Float)
    is_paper: Mapped[bool] = mapped_column(Boolean)
    order_ids: Mapped[str] = mapped_column(String, default="")
    outcome_known: Mapped[bool] = mapped_column(Boolean, default=True)

    __table_args__ = (
        Index("ix_spread_results_condition_exit", "condition_id", "exit_time"),
        Index("ix_spread_results_asset_exit", "asset", "exit_time"),
        Index("ix_spread_results_paper_exit", "is_paper", "exit_time"),
    )

    @classmethod
    def from_spread_result(cls, result: SpreadResult) -> SpreadResultRecord:
        """Create a database record from an in-memory ``SpreadResult``.

        Flatten the nested ``SpreadOpportunity`` fields and convert
        ``Decimal`` values to ``float`` for storage.

        Args:
            result: The closed trade result to persist.

        Returns:
            A new ``SpreadResultRecord`` ready for insertion.

        """
        opp = result.opportunity
        return cls(
            condition_id=opp.condition_id,
            asset=opp.asset,
            window_start_ts=opp.window_start_ts,
            window_end_ts=opp.window_end_ts,
            state=result.state.value,
            up_entry=float(result.up_entry),
            up_qty=float(result.up_qty),
            down_entry=float(result.down_entry) if result.down_entry is not None else None,
            down_qty=float(result.down_qty) if result.down_qty is not None else None,
            total_cost_basis=float(result.total_cost_basis),
            combined_price=float(opp.combined),
            margin=float(opp.margin),
            entry_time=result.entry_time,
            exit_time=result.exit_time,
            winning_side=result.winning_side,
            pnl=float(result.pnl),
            is_paper=result.is_paper,
            order_ids=",".join(result.order_ids),
            outcome_known=result.outcome_known,
        )
