"""Detect copy-worthy signals from whale trading activity.

Poll the whale_trades table incrementally, maintain a rolling window
of recent trades in memory, and identify markets where the whale has
a strong enough directional bias to copy.

Performance notes:
- Incremental polling: only fetch trades newer than last seen timestamp.
- In-memory accumulation: trades are kept in a deque and trimmed by age.
- No full-table scans: each poll is a narrow time-range query.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING

from trading_tools.apps.whale_monitor.analyser import analyse_markets
from trading_tools.apps.whale_monitor.correlator import parse_asset, parse_time_window

from .models import CopySignal

if TYPE_CHECKING:
    from trading_tools.apps.whale_monitor.models import WhaleTrade
    from trading_tools.apps.whale_monitor.repository import WhaleRepository

logger = logging.getLogger(__name__)


def _empty_deque() -> deque[WhaleTrade]:
    """Return an empty deque for dataclass default_factory."""
    return deque()


@dataclass
class SignalDetector:
    """Detect copy signals from whale trades using incremental DB polling.

    Maintain a rolling window of trades in memory. Each call to
    ``detect_signals`` fetches only new trades since the last poll,
    appends them to the window, trims expired trades, and runs bias
    analysis to find actionable signals.

    Attributes:
        repo: Async whale trade repository for DB access.
        whale_address: Proxy wallet address to monitor.
        min_bias: Minimum bias ratio to emit a signal.
        min_trades: Minimum trades per market to consider.
        lookback_seconds: Rolling window duration in seconds.

    """

    repo: WhaleRepository
    whale_address: str
    min_bias: Decimal
    min_trades: int
    lookback_seconds: int
    _last_seen_ts: int = 0
    _trades: deque[WhaleTrade] = field(default_factory=_empty_deque)

    async def detect_signals(self) -> list[CopySignal]:
        """Poll for new trades and return any actionable copy signals.

        Fetch trades newer than ``_last_seen_ts``, append to the rolling
        window, trim expired entries, then run ``analyse_markets`` and
        filter for BTC/ETH markets with future time windows and sufficient
        bias.

        Returns:
            List of ``CopySignal`` for markets that meet all thresholds.

        """
        now = int(time.time())
        start_ts = self._last_seen_ts + 1 if self._last_seen_ts else now - self.lookback_seconds

        new_trades = await self.repo.get_trades(self.whale_address, start_ts, now)

        if new_trades:
            self._trades.extend(new_trades)
            self._last_seen_ts = max(t.timestamp for t in new_trades)
            logger.info(
                "POLL +%d new trades (window=%d total)",
                len(new_trades),
                len(self._trades),
            )
        else:
            logger.debug("POLL no new trades (window=%d total)", len(self._trades))

        cutoff = now - self.lookback_seconds
        trimmed = 0
        while self._trades and self._trades[0].timestamp < cutoff:
            self._trades.popleft()
            trimmed += 1
        if trimmed:
            logger.debug("Trimmed %d expired trades from window", trimmed)

        if not self._trades:
            logger.debug("Empty rolling window — nothing to analyse")
            return []

        breakdowns = analyse_markets(list(self._trades), min_trades=self.min_trades)

        signals: list[CopySignal] = []
        skipped_asset = 0
        skipped_window = 0
        skipped_expired = 0
        skipped_bias = 0

        for bd in breakdowns:
            if bd.bias_ratio < float(self.min_bias):
                skipped_bias += 1
                continue

            asset = parse_asset(bd.title)
            if asset is None:
                skipped_asset += 1
                continue

            window = parse_time_window(bd.title, bd.first_trade_ts)
            if window is None:
                skipped_window += 1
                continue

            _start_ts, end_ts = window
            if end_ts <= now:
                skipped_expired += 1
                continue

            signals.append(
                CopySignal(
                    condition_id=bd.condition_id,
                    title=bd.title,
                    asset=asset,
                    favoured_side=bd.favoured_side,
                    bias_ratio=Decimal(str(bd.bias_ratio)),
                    trade_count=bd.trade_count,
                    window_start_ts=_start_ts,
                    window_end_ts=end_ts,
                    detected_at=now,
                )
            )

        if breakdowns:
            logger.info(
                "ANALYSE %d markets → %d signals"
                " (skipped: %d low-bias, %d non-BTC/ETH, %d no-window, %d expired)",
                len(breakdowns),
                len(signals),
                skipped_bias,
                skipped_asset,
                skipped_window,
                skipped_expired,
            )
            for sig in signals:
                logger.info(
                    "  SIGNAL %s side=%s bias=%.1f:1 trades=%d asset=%s",
                    sig.title[:50],
                    sig.favoured_side,
                    sig.bias_ratio,
                    sig.trade_count,
                    sig.asset,
                )

        return signals

    @property
    def window_size(self) -> int:
        """Return the number of trades in the current rolling window."""
        return len(self._trades)
