"""SQLAlchemy ORM models for the tick collector database.

Define the ``Tick`` table that stores individual trade events captured from
the Polymarket CLOB WebSocket market channel. The schema is designed for
efficient backtesting queries filtered by asset and time range.
"""

from sqlalchemy import BigInteger, Float, Index, Integer, String
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

    __table_args__ = (Index("ix_ticks_asset_timestamp", "asset_id", "timestamp"),)
