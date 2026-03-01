"""SQLAlchemy ORM models for the tick collector database.

Define the ``Tick`` and ``OrderBookSnapshot`` tables. ``Tick`` stores individual
trade events captured from the Polymarket CLOB WebSocket market channel.
``OrderBookSnapshot`` stores periodic order book depth data polled from the
CLOB REST API. Both schemas are designed for efficient backtesting queries
filtered by asset/token and time range.
"""

from sqlalchemy import BigInteger, Float, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base class for all tick collector ORM models."""


class Tick(Base):
    """A single trade event captured from the Polymarket WebSocket feed.

    Each row represents one ``last_trade_price`` event, storing the execution
    price, size, side, and fee information along with both the Polymarket
    timestamp and the local receive time.

    Attributes:
        id: Auto-incrementing primary key.
        asset_id: Token identifier being traded (indexed).
        condition_id: Market condition identifier (indexed).
        price: Execution price of the trade.
        size: Trade size in tokens.
        side: Trade direction, ``"BUY"`` or ``"SELL"``.
        fee_rate_bps: Fee rate in basis points.
        timestamp: Epoch milliseconds from Polymarket (indexed).
        received_at: Epoch milliseconds when the event was received locally.

    """

    __tablename__ = "ticks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    asset_id: Mapped[str] = mapped_column(String, index=True)
    condition_id: Mapped[str] = mapped_column(String, index=True)
    price: Mapped[float] = mapped_column(Float)
    size: Mapped[float] = mapped_column(Float)
    side: Mapped[str] = mapped_column(String)
    fee_rate_bps: Mapped[int] = mapped_column(Integer)
    timestamp: Mapped[int] = mapped_column(BigInteger, index=True)
    received_at: Mapped[int] = mapped_column(BigInteger)

    __table_args__ = (
        Index("ix_ticks_asset_timestamp", "asset_id", "timestamp"),
        Index("ix_ticks_condition_timestamp", "condition_id", "timestamp"),
    )


class OrderBookSnapshot(Base):
    """A periodic order book depth snapshot polled from the CLOB REST API.

    Each row stores the top N bid/ask levels as JSON for a single token at a
    point in time. The precomputed spread and midpoint enable fast filtering
    without deserializing the level arrays.

    Attributes:
        id: Auto-incrementing primary key.
        token_id: CLOB token identifier (indexed).
        timestamp: Epoch milliseconds when the snapshot was taken (indexed).
        bids_json: JSON array of ``[[price, size], ...]`` bid levels,
            ordered best-to-worst.
        asks_json: JSON array of ``[[price, size], ...]`` ask levels,
            ordered best-to-worst.
        spread: Best ask minus best bid at snapshot time.
        midpoint: Average of best bid and best ask.

    """

    __tablename__ = "order_book_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token_id: Mapped[str] = mapped_column(String, index=True)
    timestamp: Mapped[int] = mapped_column(BigInteger, index=True)
    bids_json: Mapped[str] = mapped_column(Text)
    asks_json: Mapped[str] = mapped_column(Text)
    spread: Mapped[float] = mapped_column(Float)
    midpoint: Mapped[float] = mapped_column(Float)

    __table_args__ = (Index("ix_book_token_timestamp", "token_id", "timestamp"),)
