"""Async repository for persisting and querying closed spread trade results.

Wrap SQLAlchemy async engine and session management for the spread
capture bot.  Provide methods for single-row inserts (called immediately
when a position settles) and time-range queries for analysis.  The
repository is database-agnostic — swap from SQLite to PostgreSQL by
changing the connection string.
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from trading_tools.apps.spread_capture.models import SpreadBase, SpreadResultRecord

logger = logging.getLogger(__name__)


class SpreadResultRepository:
    """Async repository for spread trade result persistence and retrieval.

    Manage an async SQLAlchemy engine and session factory.  Persist each
    settled trade immediately (not batched) so data survives crashes.
    Support time-range queries with optional filters for analysis.

    Args:
        db_url: SQLAlchemy async connection string
            (e.g. ``postgresql+asyncpg://user:pass@host/db``).

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
            await conn.run_sync(SpreadBase.metadata.create_all)
        logger.info("Spread result tables initialised")

    async def save_result(self, record: SpreadResultRecord) -> None:
        """Persist a single settled trade result within a transaction.

        Called immediately when a position settles so that data is
        durable even if the process crashes before shutdown.

        Args:
            record: The ``SpreadResultRecord`` ORM instance to insert.

        """
        async with self._session_factory() as session, session.begin():
            session.add(record)
        logger.debug("Saved spread result for %s", record.condition_id[:12])

    async def get_results(
        self,
        start_ts: int,
        end_ts: int,
        *,
        is_paper: bool | None = None,
        asset: str | None = None,
    ) -> list[SpreadResultRecord]:
        """Query spread trade results within a time range.

        Filter by ``exit_time`` to retrieve trades that settled during
        the specified window.  Optionally narrow to paper/live mode or a
        specific asset.

        Args:
            start_ts: Inclusive lower bound (epoch seconds).
            end_ts: Inclusive upper bound (epoch seconds).
            is_paper: If set, filter by paper (``True``) or live (``False``).
            asset: If set, filter by asset (e.g. ``"BTC-USD"``).

        Returns:
            List of matching ``SpreadResultRecord`` rows ordered by exit_time.

        """
        stmt = (
            select(SpreadResultRecord)
            .where(
                SpreadResultRecord.exit_time >= start_ts,
                SpreadResultRecord.exit_time <= end_ts,
            )
            .order_by(SpreadResultRecord.exit_time)
        )

        if is_paper is not None:
            stmt = stmt.where(SpreadResultRecord.is_paper == is_paper)
        if asset is not None:
            stmt = stmt.where(SpreadResultRecord.asset == asset)

        async with self._session_factory() as session:
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def close(self) -> None:
        """Dispose the async engine and release all connections."""
        await self._engine.dispose()
        logger.info("Spread result database engine disposed")
