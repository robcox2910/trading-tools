"""Orchestration wrapper for the directional trading algorithm.

Thin layer around ``DirectionalEngine`` that manages the async polling
loop, graceful shutdown, heartbeat logging, periodic summaries, and
adapter wiring.  All strategy decisions are delegated to the engine.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from trading_tools.apps.bot_framework.heartbeat import HeartbeatLogger
from trading_tools.apps.bot_framework.shutdown import GracefulShutdown
from trading_tools.clients.binance.client import BinanceClient
from trading_tools.core.models import ZERO
from trading_tools.data.providers.binance import BinanceCandleProvider
from trading_tools.data.providers.order_book_feed import OrderBookFeed

from .adapters import PaperExecution
from .engine import DirectionalEngine
from .estimator import ProbabilityEstimator
from .market_data_live import LiveMarketData

if TYPE_CHECKING:
    from collections.abc import Mapping

    from trading_tools.apps.whale_monitor.repository import WhaleRepository
    from trading_tools.clients.polymarket.client import PolymarketClient

    from .config import DirectionalConfig
    from .models import DirectionalPosition, DirectionalResult
    from .repository import DirectionalResultRepository

logger = logging.getLogger(__name__)

_SUMMARY_INTERVAL = 900  # 15 minutes


@dataclass
class DirectionalTrader:
    """Orchestrate the directional trading polling loop.

    Initialize adapters, create the engine, and run a tight async loop
    that delegates all strategy decisions to ``DirectionalEngine``.
    Handle graceful shutdown and periodic logging.

    Attributes:
        config: Algorithm configuration.
        client: Authenticated Polymarket client for CLOB data.
        live: Enable live trading (default: paper mode).

    """

    config: DirectionalConfig
    client: PolymarketClient
    live: bool = False
    _engine: DirectionalEngine | None = field(default=None, init=False, repr=False)
    _poll_count: int = field(default=0, repr=False)
    _shutdown: GracefulShutdown = field(default_factory=GracefulShutdown, init=False, repr=False)
    _heartbeat: HeartbeatLogger = field(default_factory=HeartbeatLogger, init=False, repr=False)
    _summary_due: float = field(default=0.0, init=False, repr=False)
    _binance: BinanceClient | None = field(default=None, init=False, repr=False)
    _candle_provider: BinanceCandleProvider | None = field(default=None, init=False, repr=False)
    _book_feed: OrderBookFeed | None = field(default=None, init=False, repr=False)
    _repo: DirectionalResultRepository | None = field(default=None, init=False, repr=False)
    _whale_repo: WhaleRepository | None = field(default=None, init=False, repr=False)

    async def run(self) -> None:
        """Run the polling loop until interrupted.

        Initialize adapters, create the engine, and enter the polling
        loop.  Settle remaining positions on shutdown.
        """
        self._binance = BinanceClient()
        self._candle_provider = BinanceCandleProvider(self._binance)
        self._book_feed = OrderBookFeed()
        await self._book_feed.start([])
        self._shutdown.install()

        engine = self._create_engine()
        self._engine = engine

        if self._repo is not None:
            engine.set_repository(self._repo)

        mode = "LIVE" if self.live else "PAPER"
        capital = engine.execution.get_capital()
        logger.info(
            "directional started mode=%s poll=%ds capital=$%s"
            " min_edge=%s kelly=%s entry=[%d,%d]s slugs=%s",
            mode,
            self.config.poll_interval,
            capital,
            self.config.min_edge,
            self.config.kelly_fraction,
            self.config.entry_window_end,
            self.config.entry_window_start,
            ",".join(self.config.series_slugs),
        )

        try:
            while not self._shutdown.should_stop:
                now = int(time.time())
                await engine.poll_cycle(now)
                self._poll_count += 1

                self._log_heartbeat()
                now_mono = time.monotonic()
                if now_mono >= self._summary_due:
                    self._log_periodic_summary()
                    self._summary_due = now_mono + _SUMMARY_INTERVAL

                await asyncio.sleep(self.config.poll_interval)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            self._log_summary()
            if self._book_feed is not None:  # pyright: ignore[reportUnnecessaryComparison]
                await self._book_feed.stop()
            if self._binance is not None:  # pyright: ignore[reportUnnecessaryComparison]
                await self._binance.close()

    def _create_engine(self) -> DirectionalEngine:
        """Build the engine with appropriate adapters for the current mode.

        Returns:
            A configured ``DirectionalEngine`` instance.

        """
        mode_label = "LIVE" if self.live else "PAPER"

        execution = PaperExecution(
            capital=self.config.capital,
            slippage_pct=self.config.paper_slippage_pct,
        )

        if self._candle_provider is None:
            msg = "BinanceCandleProvider must be initialised before creating engine"
            raise RuntimeError(msg)

        market_data = LiveMarketData(
            client=self.client,
            candle_provider=self._candle_provider,
            series_slugs=self.config.series_slugs,
            whale_repo=self._whale_repo,
            book_feed=self._book_feed,
        )

        estimator = ProbabilityEstimator(self.config)

        estimator_by_slug = {
            slug: ProbabilityEstimator.for_slug(self.config, slug)
            for slug in self.config.weights_by_slug
        }

        return DirectionalEngine(
            config=self.config,
            execution=execution,
            market_data=market_data,
            estimator=estimator,
            mode_label=mode_label,
            estimator_by_slug=estimator_by_slug,
        )

    def set_repository(self, repo: DirectionalResultRepository) -> None:
        """Attach a database repository for persisting trade results.

        Args:
            repo: An initialised ``DirectionalResultRepository`` instance.

        """
        self._repo = repo
        if self._engine is not None:
            self._engine.set_repository(repo)

    def set_whale_repo(self, repo: WhaleRepository) -> None:
        """Attach a whale repository for querying whale signals.

        Args:
            repo: An initialised ``WhaleRepository`` instance.

        """
        self._whale_repo = repo

    def stop(self) -> None:
        """Signal the polling loop to stop after the current cycle."""
        self._shutdown.request()

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _log_heartbeat(self) -> None:
        """Emit a heartbeat via the shared HeartbeatLogger."""
        if self._engine is None:
            return
        total_pnl = sum(r.pnl for r in self._engine.results)
        self._heartbeat.maybe_log(
            polls=self._poll_count,
            open=len(self._engine.positions),
            closed=len(self._engine.results),
            pnl=float(total_pnl),
        )

    def _log_periodic_summary(self) -> None:
        """Log a detailed session summary every 15 minutes."""
        if self._engine is None:
            return
        results = self._engine.results
        total_pnl = sum(r.pnl for r in results)
        wins = sum(1 for r in results if r.pnl > ZERO)
        losses = sum(1 for r in results if r.pnl < ZERO)
        win_rate = (wins / len(results) * 100) if results else 0.0
        capital = self._engine.execution.get_capital()

        logger.info("=" * 60)
        logger.info(
            "SUMMARY | capital=$%.2f | pnl=$%.2f | wins=%d losses=%d (%.0f%%)",
            capital,
            total_pnl,
            wins,
            losses,
            win_rate,
        )
        logger.info(
            "SUMMARY | open=%d | closed=%d | polls=%d",
            len(self._engine.positions),
            len(results),
            self._poll_count,
        )
        for cid, pos in self._engine.positions.items():
            logger.info(
                "SUMMARY |   %s %s | p_up=%.3f @ $%.4f qty=%.1f",
                pos.predicted_side,
                cid[:12],
                pos.p_up,
                pos.entry_price,
                pos.quantity,
            )
        logger.info("=" * 60)

    def _log_summary(self) -> None:
        """Log a final session summary on shutdown."""
        if self._engine is None:
            return
        total_pnl = sum(r.pnl for r in self._engine.results)
        logger.info(
            "SESSION SUMMARY polls=%d closed=%d open=%d pnl=%.4f",
            self._poll_count,
            len(self._engine.results),
            len(self._engine.positions),
            total_pnl,
        )

    # ------------------------------------------------------------------
    # Public read-only properties
    # ------------------------------------------------------------------

    @property
    def positions(self) -> Mapping[str, DirectionalPosition]:
        """Return the current open positions (read-only)."""
        if self._engine is None:
            return {}
        return self._engine.positions

    @property
    def results(self) -> list[DirectionalResult]:
        """Return all closed trade results."""
        if self._engine is None:
            return []
        return self._engine.results

    @property
    def poll_count(self) -> int:
        """Return the number of completed poll cycles."""
        return self._poll_count
