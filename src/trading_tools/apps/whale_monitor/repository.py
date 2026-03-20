"""Async repository for persisting and querying whale trades and tracked whales.

Wrap SQLAlchemy async engine and session management for the whale monitor.
Support batch inserts with transaction-hash deduplication, whale registration,
and time-range queries for strategy analysis. The repository is
database-agnostic — swap from SQLite to PostgreSQL by changing the
connection string.
"""

import logging
import time

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from trading_tools.apps.whale_monitor.models import Base, TrackedWhale, WhaleTrade

logger = logging.getLogger(__name__)


class WhaleRepository:
    """Async repository for whale trade persistence and retrieval.

    Manage an async SQLAlchemy engine and session factory. Provide methods for
    creating the schema, registering tracked whales, batch-inserting trades
    with deduplication, and querying by address and time range.

    Args:
        db_url: SQLAlchemy async connection string
            (e.g. ``sqlite+aiosqlite:///whale_data.db``).

    """

    def __init__(self, db_url: str) -> None:
        """Initialize the repository with an async database engine.

        Args:
            db_url: SQLAlchemy async connection string.

        """
        self._engine: AsyncEngine = create_async_engine(db_url, echo=False)
        self._session_factory = async_sessionmaker(
            self._engine, class_=AsyncSession, expire_on_commit=False
        )

    async def init_db(self) -> None:
        """Create all tables if they do not already exist.

        Idempotent — safe to call on every startup.
        """
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables initialised")

    async def add_whale(self, address: str, label: str) -> TrackedWhale:
        """Register a new whale address for monitoring.

        If the address already exists, return the existing record unchanged.

        Args:
            address: Proxy wallet address (stored lowercase).
            label: Human-friendly name for the whale.

        Returns:
            The existing or newly created ``TrackedWhale`` record.

        """
        addr = address.lower()
        async with self._session_factory() as session, session.begin():
            stmt = select(TrackedWhale).where(TrackedWhale.address == addr)
            result = await session.execute(stmt)
            existing = result.scalars().first()
            if existing:
                logger.info("Whale already tracked: %s (%s)", addr[:10], existing.label)
                return existing
            whale = TrackedWhale(
                address=addr,
                label=label,
                added_at=int(time.time()),
                active=True,
            )
            session.add(whale)
        logger.info("Added whale: %s (%s)", addr[:10], label)
        return whale

    async def get_active_whales(self) -> list[TrackedWhale]:
        """Return all whales with ``active=True``.

        Returns:
            List of active ``TrackedWhale`` records.

        """
        stmt = select(TrackedWhale).where(TrackedWhale.active.is_(True))
        async with self._session_factory() as session:
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_existing_hashes(self, hashes: set[str]) -> set[str]:
        """Return the subset of transaction hashes already stored.

        Args:
            hashes: Candidate transaction hashes to check.

        Returns:
            Set of hashes that already exist in the database.

        """
        if not hashes:
            return set()
        stmt = select(WhaleTrade.transaction_hash).where(WhaleTrade.transaction_hash.in_(hashes))
        async with self._session_factory() as session:
            result = await session.execute(stmt)
            return set(result.scalars().all())

    async def save_trades(self, trades: list[WhaleTrade]) -> None:
        """Batch-insert a list of whale trade records into the database.

        Args:
            trades: WhaleTrade ORM instances to persist.

        """
        if not trades:
            return
        async with self._session_factory() as session, session.begin():
            session.add_all(trades)
        logger.debug("Saved %d whale trades", len(trades))

    async def get_trades(
        self,
        address: str,
        start_ts: int,
        end_ts: int,
    ) -> list[WhaleTrade]:
        """Query whale trades for an address within a time range.

        Args:
            address: Whale proxy wallet address.
            start_ts: Inclusive lower bound (epoch seconds).
            end_ts: Inclusive upper bound (epoch seconds).

        Returns:
            List of matching ``WhaleTrade`` records ordered by timestamp.

        """
        stmt = (
            select(WhaleTrade)
            .where(
                WhaleTrade.whale_address == address.lower(),
                WhaleTrade.timestamp >= start_ts,
                WhaleTrade.timestamp <= end_ts,
            )
            .order_by(WhaleTrade.timestamp)
        )
        async with self._session_factory() as session:
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_whale_signal(
        self, condition_id: str, *, before_ts: int | None = None
    ) -> float | None:
        """Return a continuous whale directional signal for a market.

        Query BUY trades for the given condition_id, sum dollar volume
        (size * price) per outcome, and return a normalised ratio:
        ``(up_vol - down_vol) / (up_vol + down_vol)``, ranging from
        ``-1`` (all Down) to ``+1`` (all Up).

        Args:
            condition_id: Polymarket market condition identifier.
            before_ts: Only consider trades before this epoch-second
                timestamp.  Use in backtesting to prevent look-ahead bias.
                When ``None``, all trades are included (live behaviour).

        Returns:
            Continuous signal in ``[-1, 1]``, or ``None`` if no whale
            activity for this market.

        """
        filters = [
            WhaleTrade.condition_id == condition_id,
            WhaleTrade.side == "BUY",
        ]
        if before_ts is not None:
            filters.append(WhaleTrade.timestamp <= before_ts)

        stmt = (
            select(
                WhaleTrade.outcome,
                func.sum(WhaleTrade.size * WhaleTrade.price).label("dollar_vol"),
            )
            .where(*filters)
            .group_by(WhaleTrade.outcome)
        )
        async with self._session_factory() as session:
            result = await session.execute(stmt)
            rows = result.all()
            if not rows:
                return None
            vol_by_outcome: dict[str, float] = {row[0]: float(row[1]) for row in rows}
            up_vol = vol_by_outcome.get("Up", 0.0)
            down_vol = vol_by_outcome.get("Down", 0.0)
            total = up_vol + down_vol
            if total == 0.0:
                return None
            return (up_vol - down_vol) / total

    async def get_buy_trades_for_conditions(
        self,
        condition_ids: set[str],
    ) -> list[WhaleTrade]:
        """Fetch all BUY trades for a set of condition IDs in one query.

        Return every BUY-side whale trade matching any of the given
        ``condition_ids``, ordered by ``(condition_id, timestamp)``.  Designed
        for bulk pre-fetching to avoid per-window DB round-trips in grid
        search backtests.

        Args:
            condition_ids: Set of Polymarket condition identifiers to query.

        Returns:
            List of matching ``WhaleTrade`` rows sorted by
            ``(condition_id, timestamp)``.

        """
        if not condition_ids:
            return []
        stmt = (
            select(WhaleTrade)
            .where(
                WhaleTrade.condition_id.in_(condition_ids),
                WhaleTrade.side == "BUY",
            )
            .order_by(WhaleTrade.condition_id, WhaleTrade.timestamp)
        )
        async with self._session_factory() as session:
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_trade_count(self, address: str | None = None) -> int:
        """Return the total number of whale trade records.

        Args:
            address: Optional address to filter by. If ``None``, count all.

        Returns:
            Integer count of matching rows.

        """
        stmt = select(func.count()).select_from(WhaleTrade)
        if address:
            stmt = stmt.where(WhaleTrade.whale_address == address.lower())
        async with self._session_factory() as session:
            result = await session.execute(stmt)
            return result.scalar_one()

    async def close(self) -> None:
        """Dispose the async engine and release all connections."""
        await self._engine.dispose()
        logger.info("Database engine disposed")
