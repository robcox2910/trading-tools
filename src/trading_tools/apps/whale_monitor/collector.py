"""Main orchestrator for the whale trade monitor service.

Wire together the Polymarket Data API poller, whale repository, and trade
deduplication to capture every trade from tracked whale addresses. Handle
periodic polling, heartbeat logging, and graceful shutdown.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import time
from typing import TYPE_CHECKING, Any

import httpx

from trading_tools.apps.whale_monitor.models import WhaleTrade
from trading_tools.apps.whale_monitor.repository import WhaleRepository

if TYPE_CHECKING:
    from trading_tools.apps.whale_monitor.config import WhaleMonitorConfig

logger = logging.getLogger(__name__)

_HEARTBEAT_INTERVAL_SECONDS = 60
_MS_PER_SECOND = 1000
_DATA_API_BASE = "https://data-api.polymarket.com"


class WhaleMonitor:
    """Orchestrate periodic trade collection from tracked whale addresses.

    Poll the Polymarket Data API for each active whale, deduplicate by
    transaction hash, and batch-insert new trades. Log heartbeat stats
    for CloudWatch monitoring and shut down gracefully on SIGINT/SIGTERM.

    Args:
        config: Immutable whale monitor configuration.

    """

    def __init__(self, config: WhaleMonitorConfig) -> None:
        """Initialize the monitor with the given configuration.

        Args:
            config: Whale monitor configuration with DB URL, poll interval,
                and initial whale addresses.

        """
        self._config = config
        self._repo = WhaleRepository(config.db_url)
        self._shutdown = False
        self._trades_since_heartbeat = 0
        self._total_trades = 0

    async def run(self) -> None:
        """Execute the main polling loop until shutdown.

        Steps:
            1. Initialise the database schema.
            2. Register CLI-supplied whale addresses.
            3. Start the periodic heartbeat task.
            4. Poll each active whale on the configured interval.
            5. On SIGINT/SIGTERM, finish the current cycle and shut down.

        """
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGINT, self._handle_shutdown)
        loop.add_signal_handler(signal.SIGTERM, self._handle_shutdown)

        await self._repo.init_db()
        await self._register_cli_whales()

        whales = await self._repo.get_active_whales()
        if not whales:
            logger.error("No active whales to monitor — add whales first")
            await self._repo.close()
            return

        logger.info(
            "Starting whale monitor for %d whales, polling every %ds",
            len(whales),
            self._config.poll_interval_seconds,
        )

        heartbeat_task = asyncio.create_task(self._periodic_heartbeat())

        try:
            await self._poll_loop()
        finally:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
            await self._repo.close()
            logger.info(
                "Whale monitor shut down — %d total trades collected",
                self._total_trades,
            )

    async def _register_cli_whales(self) -> None:
        """Register whale addresses provided via CLI flags."""
        for addr in self._config.whales:
            await self._repo.add_whale(addr, label=addr[:10])

    async def _poll_loop(self) -> None:
        """Run the main poll cycle, sleeping between iterations."""
        while not self._shutdown:
            whales = await self._repo.get_active_whales()
            async with httpx.AsyncClient(timeout=30.0) as client:
                for whale in whales:
                    if self._shutdown:
                        break
                    try:
                        new_count = await self._poll_whale(client, whale.address)
                        if new_count > 0:
                            logger.info(
                                "Collected %d new trades for %s (%s)",
                                new_count,
                                whale.label,
                                whale.address[:10],
                            )
                    except Exception:
                        logger.exception(
                            "Failed to poll whale %s (%s)",
                            whale.label,
                            whale.address[:10],
                        )

            if not self._shutdown:
                await asyncio.sleep(self._config.poll_interval_seconds)

    async def _poll_whale(self, client: httpx.AsyncClient, address: str) -> int:
        """Fetch and store new trades for a single whale address.

        Paginate through the Data API up to ``max_offset``, processing
        each page immediately to minimise peak memory usage. Deduplicate
        by transaction hash against existing records and within the
        current poll cycle.

        Args:
            client: Async HTTP client for API requests.
            address: Whale proxy wallet address.

        Returns:
            Number of new trades inserted.

        """
        total_new = 0
        seen_hashes: set[str] = set()
        limit = self._config.api_limit
        now_ms = _now_ms()

        for offset in range(0, self._config.max_offset + 1, limit):
            url = f"{_DATA_API_BASE}/trades"
            params: dict[str, str | int] = {
                "user": address,
                "limit": limit,
                "offset": offset,
                "takerOnly": "false",
            }
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            page: list[dict[str, Any]] = resp.json()
            if not page:
                break

            page_new = await self._process_page(page, address, seen_hashes, now_ms)
            total_new += page_new

            if len(page) < limit:
                break

        return total_new

    async def _process_page(
        self,
        page: list[dict[str, Any]],
        address: str,
        seen_hashes: set[str],
        now_ms: int,
    ) -> int:
        """Deduplicate and persist a single page of raw trades.

        Check candidate hashes against the database and the running
        ``seen_hashes`` set, then batch-insert any genuinely new trades.
        Each page is processed and discarded before fetching the next,
        keeping peak memory proportional to one page (~1000 records).

        Args:
            page: Raw trade dicts from one API page.
            address: Whale proxy wallet address.
            seen_hashes: Hashes already seen in this poll cycle
                (mutated in-place to track cross-page duplicates).
            now_ms: Collection timestamp in epoch milliseconds.

        Returns:
            Number of new trades inserted from this page.

        """
        candidate_hashes = {
            str(t.get("transactionHash", ""))
            for t in page
            if str(t.get("transactionHash", "")) not in seen_hashes
        }
        if not candidate_hashes:
            return 0

        existing_hashes = await self._repo.get_existing_hashes(candidate_hashes)
        skip = existing_hashes | (seen_hashes & candidate_hashes)

        new_trades: list[WhaleTrade] = []
        for raw in page:
            tx_hash = str(raw.get("transactionHash", ""))
            if tx_hash in skip or tx_hash in seen_hashes:
                continue
            seen_hashes.add(tx_hash)
            trade = _parse_trade(raw, address, now_ms)
            if trade:
                new_trades.append(trade)

        if new_trades:
            await self._repo.save_trades(new_trades)
            self._trades_since_heartbeat += len(new_trades)
            self._total_trades += len(new_trades)

        return len(new_trades)

    def _handle_shutdown(self) -> None:
        """Set the shutdown flag for graceful exit on SIGINT/SIGTERM."""
        logger.info("Shutdown signal received")
        self._shutdown = True

    async def _periodic_heartbeat(self) -> None:
        """Log collection stats at regular intervals for monitoring.

        Emit a structured log line compatible with CloudWatch metric
        filters containing trades-per-minute, total stored, and whale
        count.
        """
        try:
            await self._periodic_heartbeat_inner()
        except asyncio.CancelledError:
            return

    async def _periodic_heartbeat_inner(self) -> None:
        """Execute the heartbeat loop body (separated for CancelledError handling)."""
        while not self._shutdown:
            await asyncio.sleep(_HEARTBEAT_INTERVAL_SECONDS)
            if self._shutdown:
                break
            whales = await self._repo.get_active_whales()
            total = await self._repo.get_trade_count()
            logger.info(
                "[WHALE-MONITOR] trades_last_min=%d total_stored=%d whales=%d",
                self._trades_since_heartbeat,
                total,
                len(whales),
            )
            self._trades_since_heartbeat = 0


def _parse_trade(
    raw: dict[str, Any],
    address: str,
    collected_at_ms: int,
) -> WhaleTrade | None:
    """Parse a raw API trade dict into a WhaleTrade ORM instance.

    Args:
        raw: Raw trade dictionary from the Polymarket Data API.
        address: Whale proxy wallet address.
        collected_at_ms: Epoch milliseconds when the trade was fetched.

    Returns:
        A ``WhaleTrade`` instance, or ``None`` if the record is malformed.

    """
    try:
        return WhaleTrade(
            whale_address=address.lower(),
            transaction_hash=str(raw["transactionHash"]),
            side=str(raw.get("side", "")),
            asset_id=str(raw.get("asset_id", raw.get("assetId", ""))),
            condition_id=str(raw.get("condition_id", raw.get("conditionId", ""))),
            size=float(raw.get("size", 0)),
            price=float(raw.get("price", 0)),
            timestamp=int(raw.get("timestamp", 0)),
            title=str(raw.get("market", raw.get("title", ""))),
            slug=str(raw.get("slug", raw.get("market_slug", ""))),
            outcome=str(raw.get("outcome", "")),
            outcome_index=int(raw.get("outcome_index", raw.get("outcomeIndex", 0))),
            collected_at=collected_at_ms,
        )
    except (KeyError, ValueError, TypeError):
        logger.debug("Skipping malformed trade record: %s", raw)
        return None


def _now_ms() -> int:
    """Return the current time as epoch milliseconds.

    Returns:
        Integer epoch milliseconds.

    """
    return int(time.time() * _MS_PER_SECOND)
