"""Async repository for persisting and querying closed copy trade results.

Wrap SQLAlchemy async engine and session management for the whale
copy-trader. Provide methods for single-row inserts (called immediately
when a position closes) and time-range queries for backtesting analysis.
The repository is database-agnostic — swap from SQLite to PostgreSQL by
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

from trading_tools.apps.whale_copy_trader.models import Base, CopyResultRecord

logger = logging.getLogger(__name__)


class CopyResultRepository:
    """Async repository for copy trade result persistence and retrieval.

    Manage an async SQLAlchemy engine and session factory. Persist each
    closed trade immediately (not batched) so data survives crashes.
    Support time-range queries with optional filters for backtesting
    different ``max_spread_cost`` thresholds against historical data.

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
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Copy result tables initialised")

    async def save_result(self, record: CopyResultRecord) -> None:
        """Persist a single closed trade result within a transaction.

        Called immediately when a position closes so that data is
        durable even if the process crashes before shutdown.

        Args:
            record: The ``CopyResultRecord`` ORM instance to insert.

        """
        async with self._session_factory() as session, session.begin():
            session.add(record)
        logger.debug("Saved copy result for %s", record.condition_id[:12])

    async def get_results(
        self,
        start_ts: int,
        end_ts: int,
        *,
        is_paper: bool | None = None,
        asset: str | None = None,
    ) -> list[CopyResultRecord]:
        """Query copy trade results within a time range.

        Filter by ``exit_time`` to retrieve trades that closed during
        the specified window. Optionally narrow to paper/live mode or a
        specific asset.

        Args:
            start_ts: Inclusive lower bound (epoch seconds).
            end_ts: Inclusive upper bound (epoch seconds).
            is_paper: If set, filter by paper (``True``) or live (``False``).
            asset: If set, filter by asset (e.g. ``"BTC-USD"``).

        Returns:
            List of matching ``CopyResultRecord`` rows ordered by exit_time.

        """
        stmt = (
            select(CopyResultRecord)
            .where(
                CopyResultRecord.exit_time >= start_ts,
                CopyResultRecord.exit_time <= end_ts,
            )
            .order_by(CopyResultRecord.exit_time)
        )

        if is_paper is not None:
            stmt = stmt.where(CopyResultRecord.is_paper == is_paper)
        if asset is not None:
            stmt = stmt.where(CopyResultRecord.asset == asset)

        async with self._session_factory() as session:
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def close(self) -> None:
        """Dispose the async engine and release all connections."""
        await self._engine.dispose()
        logger.info("Copy result database engine disposed")
