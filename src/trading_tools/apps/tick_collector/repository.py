"""Async repository for persisting and querying tick and order book records.

Wrap SQLAlchemy async engine and session management for the tick collector.
Support batch inserts for high-throughput WebSocket ingestion and time-range
queries for backtesting. Order book snapshot methods provide nearest-match
lookups for enriching backtest snapshots with real depth data. The repository
is database-agnostic — swap from SQLite to PostgreSQL by changing the
connection string.
"""

import logging

from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from trading_tools.apps.tick_collector.models import Base, MarketMetadata, OrderBookSnapshot, Tick

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

    async def save_order_book_snapshots(
        self,
        snapshots: list[OrderBookSnapshot],
    ) -> None:
        """Batch-insert a list of order book snapshot records.

        Args:
            snapshots: OrderBookSnapshot ORM instances to persist.

        """
        if not snapshots:
            return
        async with self._session_factory() as session, session.begin():
            session.add_all(snapshots)
        logger.debug("Saved %d order book snapshots", len(snapshots))

    async def get_order_book_snapshots_in_range(
        self,
        token_id: str,
        start_ms: int,
        end_ms: int,
    ) -> list[OrderBookSnapshot]:
        """Query order book snapshots for a token within a time range.

        Args:
            token_id: CLOB token identifier to filter on.
            start_ms: Inclusive lower bound (epoch milliseconds).
            end_ms: Inclusive upper bound (epoch milliseconds).

        Returns:
            List of matching snapshots ordered by timestamp ascending.

        """
        stmt = (
            select(OrderBookSnapshot)
            .where(
                OrderBookSnapshot.token_id == token_id,
                OrderBookSnapshot.timestamp >= start_ms,
                OrderBookSnapshot.timestamp <= end_ms,
            )
            .order_by(OrderBookSnapshot.timestamp)
        )
        async with self._session_factory() as session:
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_all_book_snapshots_in_range(
        self,
        start_ms: int,
        end_ms: int,
    ) -> list[OrderBookSnapshot]:
        """Fetch all order book snapshots across all tokens in a time range.

        Return every snapshot between ``start_ms`` and ``end_ms`` inclusive,
        ordered by token_id then timestamp.  Designed for bulk pre-fetching
        to avoid per-window DB round-trips in grid search backtests.

        Args:
            start_ms: Inclusive lower bound (epoch milliseconds).
            end_ms: Inclusive upper bound (epoch milliseconds).

        Returns:
            List of all ``OrderBookSnapshot`` rows in the range, sorted by
            ``(token_id, timestamp)``.

        """
        stmt = (
            select(OrderBookSnapshot)
            .where(
                OrderBookSnapshot.timestamp >= start_ms,
                OrderBookSnapshot.timestamp <= end_ms,
            )
            .order_by(OrderBookSnapshot.token_id, OrderBookSnapshot.timestamp)
        )
        async with self._session_factory() as session:
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_nearest_book_snapshot(
        self,
        token_id: str,
        timestamp_ms: int,
        tolerance_ms: int = 5000,
    ) -> OrderBookSnapshot | None:
        """Find the order book snapshot nearest to a given timestamp.

        Search within ``tolerance_ms`` milliseconds of the target timestamp
        and return the closest match by absolute time difference.

        Args:
            token_id: CLOB token identifier to filter on.
            timestamp_ms: Target epoch milliseconds.
            tolerance_ms: Maximum allowed distance in milliseconds.

        Returns:
            The nearest ``OrderBookSnapshot``, or ``None`` if no snapshot
            exists within the tolerance window.

        """
        stmt = (
            select(OrderBookSnapshot)
            .where(
                OrderBookSnapshot.token_id == token_id,
                OrderBookSnapshot.timestamp >= timestamp_ms - tolerance_ms,
                OrderBookSnapshot.timestamp <= timestamp_ms + tolerance_ms,
            )
            .order_by(func.abs(OrderBookSnapshot.timestamp - timestamp_ms))
            .limit(1)
        )
        async with self._session_factory() as session:
            result = await session.execute(stmt)
            return result.scalars().first()

    async def save_market_metadata(self, metadata: MarketMetadata) -> None:
        """Insert or update a market metadata record (upsert by condition_id).

        Use merge to handle both new inserts and updates for markets
        that are re-discovered with updated timestamps.

        Args:
            metadata: ``MarketMetadata`` ORM instance to persist.

        """
        async with self._session_factory() as session, session.begin():
            await session.merge(metadata)
        logger.debug("Saved market metadata for %s", metadata.condition_id[:12])

    async def save_market_metadata_batch(self, batch: list[MarketMetadata]) -> None:
        """Insert or update a batch of market metadata records.

        Args:
            batch: List of ``MarketMetadata`` ORM instances to persist.

        """
        if not batch:
            return
        async with self._session_factory() as session, session.begin():
            for m in batch:
                await session.merge(m)
        logger.debug("Saved %d market metadata records", len(batch))

    async def get_market_metadata_in_range(
        self,
        start_ts: int,
        end_ts: int,
        *,
        asset: str | None = None,
        series_slug: str | None = None,
    ) -> list[MarketMetadata]:
        """Query market metadata for windows overlapping a time range.

        Return markets whose window overlaps [start_ts, end_ts] — i.e.
        ``window_start_ts < end_ts AND window_end_ts > start_ts``.

        Args:
            start_ts: Inclusive lower bound (epoch seconds).
            end_ts: Inclusive upper bound (epoch seconds).
            asset: If set, filter by asset (e.g. ``"BTC-USD"``).
            series_slug: If set, filter by series slug.

        Returns:
            List of matching ``MarketMetadata`` rows ordered by window_start_ts.

        """
        stmt = (
            select(MarketMetadata)
            .where(
                MarketMetadata.window_start_ts < end_ts,
                MarketMetadata.window_end_ts > start_ts,
            )
            .order_by(MarketMetadata.window_start_ts)
        )

        if asset is not None:
            stmt = stmt.where(MarketMetadata.asset == asset)
        if series_slug is not None:
            stmt = stmt.where(MarketMetadata.series_slug == series_slug)

        async with self._session_factory() as session:
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def close(self) -> None:
        """Dispose the async engine and release all connections."""
        await self._engine.dispose()
        logger.info("Database engine disposed")
