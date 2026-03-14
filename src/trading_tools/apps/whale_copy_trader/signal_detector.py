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

        cutoff = now - self.lookback_seconds
        while self._trades and self._trades[0].timestamp < cutoff:
            self._trades.popleft()

        if not self._trades:
            return []

        breakdowns = analyse_markets(list(self._trades), min_trades=self.min_trades)

        signals: list[CopySignal] = []
        for bd in breakdowns:
            if bd.bias_ratio < float(self.min_bias):
                continue

            asset = parse_asset(bd.title)
            if asset is None:
                continue

            window = parse_time_window(bd.title, bd.first_trade_ts)
            if window is None:
                continue

            _start_ts, end_ts = window
            if end_ts <= now:
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

        return signals
