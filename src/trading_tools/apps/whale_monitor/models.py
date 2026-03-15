"""SQLAlchemy ORM models for the whale trade monitor database.

Define the ``TrackedWhale`` and ``WhaleTrade`` tables. ``TrackedWhale`` stores
the proxy wallet addresses and labels of monitored traders. ``WhaleTrade``
stores individual trade records fetched from the Polymarket Data API,
deduplicated by transaction hash.
"""

from sqlalchemy import BigInteger, Boolean, Float, Index, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base class for all whale monitor ORM models."""


class TrackedWhale(Base):
    """A trader whose Polymarket activity is being monitored.

    Each row represents a unique proxy wallet address with a friendly
    label and an active toggle to enable or disable polling.

    Attributes:
        id: Auto-incrementing primary key.
        address: Proxy wallet address (unique, indexed, stored lowercase).
        label: Human-friendly name (e.g. ``"Wry-Leaker"``).
        added_at: Epoch seconds when the whale was first tracked.
        active: Whether to include this whale in polling cycles.

    """

    __tablename__ = "tracked_whales"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    address: Mapped[str] = mapped_column(String, unique=True, index=True)
    label: Mapped[str] = mapped_column(String)
    added_at: Mapped[int] = mapped_column(BigInteger)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class WhaleTrade(Base):
    """A single trade record fetched from the Polymarket Data API.

    Each row represents one trade execution, deduplicated by the unique
    ``transaction_hash``. Stores market context (title, slug, outcome)
    alongside execution details (side, size, price).

    Attributes:
        id: Auto-incrementing primary key.
        whale_address: Proxy wallet address of the trader (indexed).
        transaction_hash: Unique on-chain transaction hash for deduplication.
        side: Trade direction, ``"BUY"`` or ``"SELL"``.
        asset_id: Token asset identifier.
        condition_id: Market condition identifier (indexed).
        size: Token quantity traded.
        price: Execution price.
        timestamp: Polymarket epoch seconds when the trade occurred (indexed).
        title: Human-readable market title.
        slug: Market URL slug.
        outcome: Outcome label (e.g. ``"Up"``, ``"Down"``, ``"Yes"``, ``"No"``).
        outcome_index: Numeric outcome index (0 or 1).
        collected_at: Epoch milliseconds when we fetched this record.

    """

    __tablename__ = "whale_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    whale_address: Mapped[str] = mapped_column(String, index=True)
    transaction_hash: Mapped[str] = mapped_column(String, unique=True)
    side: Mapped[str] = mapped_column(String)
    asset_id: Mapped[str] = mapped_column(String)
    condition_id: Mapped[str] = mapped_column(String, index=True)
    size: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    timestamp: Mapped[int] = mapped_column(BigInteger, index=True)
    title: Mapped[str] = mapped_column(String)
    slug: Mapped[str] = mapped_column(String)
    outcome: Mapped[str] = mapped_column(String)
    outcome_index: Mapped[int] = mapped_column(Integer)
    collected_at: Mapped[int] = mapped_column(BigInteger)

    __table_args__ = (
        Index("ix_whale_trades_address_timestamp", "whale_address", "timestamp"),
        Index("ix_whale_trades_condition_timestamp", "condition_id", "timestamp"),
    )

    def __str__(self) -> str:
        """Return a concise human-readable summary of this trade.

        Format: ``<SIDE> <size> @ <price> | <outcome> | <title> | <timestamp>``

        Returns:
            Single-line string representation.

        """
        return (
            f"{self.side} {self.size:.2f} @ {self.price:.4f}"
            f" | {self.outcome}"
            f" | {self.title}"
            f" | ts={self.timestamp}"
        )
