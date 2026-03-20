"""Directional entry + opportunistic hedge trading wrapper.

Thin orchestration layer around ``SpreadEngine`` that manages the
async polling loop, shutdown handling, heartbeat logging, and adapter
wiring.  All strategy decisions are delegated to the engine.

Three-phase fill logic (handled by the engine):
  Phase 0 — Signal: look back ``signal_delay_seconds`` of Binance
      1-min candles to determine primary direction.
  Phase 1 — Directional entry: fill the primary side aggressively.
  Phase 2 — Opportunistic hedge: fill the secondary side when cheap.
  Phase 3 — Cutoff: stop all fills after ``max_fill_age_pct``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from trading_tools.apps.bot_framework.balance_manager import BalanceManager
from trading_tools.apps.bot_framework.heartbeat import HeartbeatLogger
from trading_tools.apps.bot_framework.order_executor import OrderExecutor
from trading_tools.apps.bot_framework.redeemer import PositionRedeemer
from trading_tools.apps.bot_framework.shutdown import GracefulShutdown
from trading_tools.clients.binance.client import BinanceClient
from trading_tools.core.models import ZERO

from .adapters import LiveExecution, LiveMarketData, PaperExecution
from .engine import SpreadEngine
from .market_scanner import MarketScanner
from .models import AccumulatingPosition, PositionState, SpreadResult
from .ports import ExecutionPort

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from trading_tools.clients.polymarket.client import PolymarketClient

    from .config import SpreadCaptureConfig
    from .repository import SpreadResultRepository

logger = logging.getLogger(__name__)

_BALANCE_REFRESH_POLLS = 60
_SUMMARY_INTERVAL = 900  # 15 minutes


@dataclass
class AccumulatingTrader:
    """Directional entry + opportunistic hedge engine for Polymarket Up/Down markets.

    Orchestrate the polling loop, shutdown, heartbeat, and summary
    logging.  All fill/hedge/settlement decisions are delegated to
    ``SpreadEngine`` via port-based adapters.

    Attributes:
        config: Immutable service configuration.
        live: Enable live trading (requires ``client``).
        client: Authenticated Polymarket client for CLOB data and orders.

    """

    config: SpreadCaptureConfig
    live: bool = False
    client: PolymarketClient | None = None
    whale_addresses: tuple[str, ...] = ()
    _scanner: MarketScanner | None = field(default=None, repr=False)
    _binance: BinanceClient | None = field(default=None, repr=False)
    _engine: SpreadEngine | None = field(default=None, init=False, repr=False)
    _poll_count: int = field(default=0, repr=False)
    _shutdown: GracefulShutdown = field(default_factory=GracefulShutdown, init=False, repr=False)
    _heartbeat: HeartbeatLogger = field(default_factory=HeartbeatLogger, init=False, repr=False)
    _summary_due: float = field(default=0.0, init=False, repr=False)
    _redeemer: PositionRedeemer | None = field(default=None, init=False, repr=False)
    _executor: OrderExecutor | None = field(default=None, init=False, repr=False)
    _balance_manager: BalanceManager | None = field(default=None, init=False, repr=False)
    _repo: SpreadResultRepository | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        """Initialize shared services when running in live mode."""
        if self.live and self.client is not None:
            self._redeemer = PositionRedeemer(client=self.client)
            self._executor = OrderExecutor(
                client=self.client,
                use_market_orders=self.config.use_market_orders,
            )
            self._balance_manager = BalanceManager(client=self.client)

    async def run(self) -> None:
        """Run the polling loop until interrupted.

        Initialize adapters, create the engine, and enter a tight async
        loop that delegates all strategy decisions to ``SpreadEngine``.
        """
        if self.client is None:
            msg = "PolymarketClient is required — pass client at construction"
            raise RuntimeError(msg)

        self._scanner = MarketScanner(
            client=self.client,
            series_slugs=self.config.series_slugs,
            max_combined_cost=self.config.max_combined_cost,
            min_spread_margin=self.config.min_spread_margin,
            max_window_seconds=self.config.max_window_seconds,
            max_entry_age_pct=self.config.max_entry_age_pct,
            rediscovery_interval=self.config.rediscovery_interval,
            fee_rate=self.config.fee_rate,
            fee_exponent=self.config.fee_exponent,
        )
        self._binance = BinanceClient()
        self._shutdown.install()

        if self.live and self._balance_manager is not None:
            await self._balance_manager.refresh()

        engine = self._create_engine()
        self._engine = engine

        if self._repo is not None:
            engine.set_repo(self._repo)

        engine.init_capital()

        mode = "LIVE" if self.live else "PAPER"
        capital = engine.execution.get_capital()
        logger.info(
            "accumulate started mode=%s poll=%ds capital=$%s"
            " signal_delay=%ds hedge=%.2f→%.2f max_imbal=%.1f"
            " fill_size=%s max_open=%d slugs=%s",
            mode,
            self.config.poll_interval,
            capital,
            self.config.signal_delay_seconds,
            self.config.hedge_start_threshold,
            self.config.hedge_end_threshold,
            self.config.max_imbalance_ratio,
            self.config.fill_size_tokens,
            self.config.max_open_positions,
            ",".join(self.config.series_slugs),
        )

        try:
            while not self._shutdown.should_stop:
                now = int(time.time())
                await engine.poll_cycle(now)
                self._poll_count += 1

                if (
                    self.live
                    and self._balance_manager is not None
                    and self._poll_count % _BALANCE_REFRESH_POLLS == 0
                ):
                    await self._balance_manager.refresh()

                self._log_heartbeat()
                now_mono = time.monotonic()
                if now_mono >= self._summary_due:
                    self._log_periodic_summary()
                    self._summary_due = now_mono + _SUMMARY_INTERVAL

                if self._redeemer is not None:
                    await self._redeemer.redeem_if_available()

                await asyncio.sleep(self.config.poll_interval)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            self._log_summary()
            if self._binance is not None:  # pyright: ignore[reportUnnecessaryComparison]
                await self._binance.close()

    def _create_engine(self) -> SpreadEngine:
        """Build the engine with appropriate adapters for the current mode.

        Returns:
            A configured ``SpreadEngine`` instance.

        """
        mode_label = "LIVE" if self.live else "PAPER"

        execution: ExecutionPort
        if self.live and self._executor is not None and self._balance_manager is not None:
            execution = LiveExecution(
                executor=self._executor,
                balance_manager=self._balance_manager,
                committed_capital_fn=lambda: sum(
                    (
                        p.total_cost_basis
                        for p in (self._engine.positions if self._engine else {}).values()
                    ),
                    start=ZERO,
                ),
            )
        else:
            execution = PaperExecution(
                base_capital=self.config.capital,
                slippage_pct=self.config.paper_slippage_pct,
                compound_profits=self.config.compound_profits,
                committed_capital_fn=lambda: sum(
                    (
                        p.total_cost_basis
                        for p in (self._engine.positions if self._engine else {}).values()
                    ),
                    start=ZERO,
                ),
                realised_pnl_fn=lambda: sum(
                    (r.pnl for r in (self._engine.results if self._engine else [])),
                    start=ZERO,
                ),
            )

        if self._scanner is None or self._binance is None or self.client is None:
            msg = "Scanner, Binance, and client must be initialised before creating engine"
            raise RuntimeError(msg)

        market_data = LiveMarketData(
            scanner=self._scanner,
            client=self.client,
            binance=self._binance,
            live=self.live,
            _whale_addresses=self.whale_addresses,
        )

        return SpreadEngine(
            config=self.config,
            execution=execution,
            market_data=market_data,
            mode_label=mode_label,
        )

    def set_repo(self, repo: SpreadResultRepository) -> None:
        """Attach a database repository for persisting settled trade results.

        Args:
            repo: An initialised ``SpreadResultRepository`` instance.

        """
        self._repo = repo
        if self._engine is not None:
            self._engine.set_repo(repo)

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
        total_pnl = self._engine.total_pnl
        scanner_markets = self._scanner.known_market_count if self._scanner else 0
        self._heartbeat.maybe_log(
            polls=self._poll_count,
            known_markets=scanner_markets,
            accumulating=sum(
                1 for p in self._engine.positions.values() if p.state == PositionState.ACCUMULATING
            ),
            closed=len(self._engine.results),
            pnl=float(total_pnl),
        )

    def _log_periodic_summary(self) -> None:
        """Log a detailed session summary every 15 minutes."""
        if self._engine is None:
            return
        total_pnl = self._engine.total_pnl
        results = self._engine.results
        wins = sum(1 for r in results if r.pnl > ZERO)
        losses = sum(1 for r in results if r.pnl < ZERO)
        win_rate = (wins / len(results) * 100) if results else 0.0
        capital = self._engine.execution.get_capital()

        logger.info("=" * 60)
        logger.info(
            "SUMMARY | capital=$%.2f | pnl=$%.2f | hwm=$%.2f | wins=%d losses=%d (%.0f%%)",
            capital,
            total_pnl,
            self._engine.high_water_mark,
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
                "SUMMARY |   %s %s combined_vwap=%.4f paired=%.1f"
                " up=%.1f@%.4f down=%.1f@%.4f cost=$%.2f",
                pos.state.value.upper(),
                cid[:12],
                pos.combined_vwap,
                pos.paired_quantity,
                pos.up_leg.quantity,
                pos.up_leg.entry_price,
                pos.down_leg.quantity,
                pos.down_leg.entry_price,
                pos.total_cost_basis,
            )
        logger.info("=" * 60)

    def _log_summary(self) -> None:
        """Log a final session summary on shutdown."""
        if self._engine is None:
            return
        total_pnl = self._engine.total_pnl
        logger.info(
            "SESSION SUMMARY polls=%d closed=%d open=%d pnl=%.4f",
            self._poll_count,
            len(self._engine.results),
            len(self._engine.positions),
            total_pnl,
        )

    # ------------------------------------------------------------------
    # Public read-only properties (backward compat)
    # ------------------------------------------------------------------

    @property
    def positions(self) -> Mapping[str, AccumulatingPosition]:
        """Return the current open positions (read-only copy)."""
        if self._engine is None:
            return {}
        return self._engine.positions

    @property
    def results(self) -> Sequence[SpreadResult]:
        """Return all closed trade results (read-only copy)."""
        if self._engine is None:
            return []
        return self._engine.results

    @property
    def poll_count(self) -> int:
        """Return the number of completed poll cycles."""
        return self._poll_count
