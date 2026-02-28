"""Async repository for persisting and querying tick records.

Wrap SQLAlchemy async engine and session management for the tick collector.
Support batch inserts for high-throughput WebSocket ingestion and time-range
queries for backtesting. The repository is database-agnostic — swap from
SQLite to PostgreSQL by changing the connection string.
"""

import logging

from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from trading_tools.apps.tick_collector.models import Base, Tick

logger = logging.getLogger(__name__)


class TickRepository:
    """Async repository for tick record persistence and retrieval.

    Manage an async SQLAlchemy engine and session factory. Provide methods for
    creating the schema, batch-inserting ticks, querying by time range, and
    retrieving aggregate counts.

    Args:
        db_url: SQLAlchemy async connection string
            (e.g. ``sqlite+aiosqlite:///ticks.db``).

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

    async def save_ticks(self, ticks: list[Tick]) -> None:
        """Batch-insert a list of tick records into the database.

        Args:
            ticks: Tick ORM instances to persist.

        """
        if not ticks:
            return
        async with self._session_factory() as session, session.begin():
            session.add_all(ticks)
        logger.debug("Saved %d ticks", len(ticks))

    async def get_ticks(self, asset_id: str, start_ms: int, end_ms: int) -> list[Tick]:
        """Query tick records for a given asset within a time range.

        Args:
            asset_id: Token identifier to filter on.
            start_ms: Inclusive lower bound (epoch milliseconds).
            end_ms: Inclusive upper bound (epoch milliseconds).

        Returns:
            List of matching ``Tick`` records ordered by timestamp ascending.

        """
        stmt = (
            select(Tick)
            .where(
                Tick.asset_id == asset_id,
                Tick.timestamp >= start_ms,
                Tick.timestamp <= end_ms,
            )
            .order_by(Tick.timestamp)
        )
        async with self._session_factory() as session:
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_tick_count(self) -> int:
        """Return the total number of tick records in the database.

        Returns:
            Integer count of all rows in the ``ticks`` table.

        """
        stmt = select(func.count()).select_from(Tick)
        async with self._session_factory() as session:
            result = await session.execute(stmt)
            return result.scalar_one()

    async def get_distinct_condition_ids(
        self,
        start_ms: int,
        end_ms: int,
    ) -> list[str]:
        """Return unique condition IDs that have ticks in a time range.

        Args:
            start_ms: Inclusive lower bound (epoch milliseconds).
            end_ms: Inclusive upper bound (epoch milliseconds).

        Returns:
            List of distinct ``condition_id`` values, sorted alphabetically.

        """
        stmt = (
            select(distinct(Tick.condition_id))
            .where(Tick.timestamp >= start_ms, Tick.timestamp <= end_ms)
            .order_by(Tick.condition_id)
        )
        async with self._session_factory() as session:
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_ticks_by_condition(
        self,
        condition_id: str,
        start_ms: int,
        end_ms: int,
    ) -> list[Tick]:
        """Query tick records for a given condition within a time range.

        Args:
            condition_id: Market condition identifier to filter on.
            start_ms: Inclusive lower bound (epoch milliseconds).
            end_ms: Inclusive upper bound (epoch milliseconds).

        Returns:
            List of matching ``Tick`` records ordered by timestamp ascending.

        """
        stmt = (
            select(Tick)
            .where(
                Tick.condition_id == condition_id,
                Tick.timestamp >= start_ms,
                Tick.timestamp <= end_ms,
            )
            .order_by(Tick.timestamp)
        )
        async with self._session_factory() as session:
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def close(self) -> None:
        """Dispose the async engine and release all connections."""
        await self._engine.dispose()
        logger.info("Database engine disposed")
