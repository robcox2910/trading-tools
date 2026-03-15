"""Detect copy-worthy signals from whale trading activity.

Poll the Polymarket Data API directly for the whale's latest trades,
maintain a rolling window in memory, and identify markets where the
whale has a strong enough directional bias to copy.

Direct API polling eliminates the latency of the whale monitor DB
pipeline (API → collector → PostgreSQL → query), giving the copy bot
near-real-time visibility into the whale's trades.

Performance notes:
- Incremental deduplication: track seen transaction hashes to avoid
  processing the same trade twice.
- In-memory accumulation: trades are kept in a deque and trimmed by age.
- Single HTTP request per poll: fetch the latest N trades directly.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import httpx

from trading_tools.apps.whale_monitor.analyser import MarketBreakdown, analyse_markets
from trading_tools.apps.whale_monitor.correlator import parse_asset, parse_time_window
from trading_tools.apps.whale_monitor.models import WhaleTrade

from .models import CopySignal

logger = logging.getLogger(__name__)

_DATA_API_BASE = "https://data-api.polymarket.com"
_API_LIMIT = 200
_MS_PER_SECOND = 1000


def _empty_deque() -> deque[WhaleTrade]:
    """Return an empty deque for dataclass default_factory."""
    return deque()


def _empty_str_set() -> set[str]:
    """Return an empty set[str] for dataclass default_factory."""
    return set()


@dataclass
class SignalDetector:
    """Detect copy signals by polling the Polymarket Data API directly.

    Fetch the whale's latest trades via HTTP, deduplicate by transaction
    hash, maintain a rolling window in memory, and run bias analysis to
    find actionable signals.

    Attributes:
        whale_address: Proxy wallet address to monitor.
        min_bias: Minimum bias ratio to emit a signal.
        min_trades: Minimum trades per market to consider.
        lookback_seconds: Rolling window duration in seconds.
        min_time_to_start: Minimum seconds before window opens to act.
        max_window_seconds: Maximum market window duration (0 = no limit).

    """

    whale_address: str
    min_bias: Decimal
    min_trades: int
    lookback_seconds: int
    min_time_to_start: int = 60
    max_window_seconds: int = 0
    _trades: deque[WhaleTrade] = field(default_factory=_empty_deque)
    _seen_hashes: set[str] = field(default_factory=_empty_str_set)
    _http_client: httpx.AsyncClient | None = field(default=None, repr=False)

    async def _get_client(self) -> httpx.AsyncClient:
        """Return the shared HTTP client, creating it lazily.

        Returns:
            An httpx.AsyncClient configured with reasonable timeouts.

        """
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        return self._http_client

    async def close(self) -> None:
        """Close the HTTP client if open."""
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    async def detect_signals(self) -> list[CopySignal]:
        """Poll the Data API for new trades and return actionable copy signals.

        Fetch the whale's latest trades, deduplicate, append to the
        rolling window, trim expired entries, then run ``analyse_markets``
        and filter for BTC/ETH markets with future time windows and
        sufficient bias.

        Returns:
            List of ``CopySignal`` for markets that meet all thresholds.

        """
        now = int(time.time())

        new_trades = await self._fetch_recent_trades()

        if new_trades:
            self._trades.extend(new_trades)
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
            self._seen_hashes = {
                f"{t.transaction_hash}:{t.asset_id}:{t.size}" for t in self._trades
            }
            logger.debug("Trimmed %d expired trades from window", trimmed)

        if not self._trades:
            logger.debug("Empty rolling window — nothing to analyse")
            return []

        breakdowns_df = analyse_markets(list(self._trades), min_trades=self.min_trades)
        breakdowns = [
            MarketBreakdown(
                condition_id=str(row.condition_id),
                title=str(row.title),
                slug=str(row.slug),
                up_volume=float(row.up_volume),  # type: ignore[arg-type]
                down_volume=float(row.down_volume),  # type: ignore[arg-type]
                up_size=float(row.up_size),  # type: ignore[arg-type]
                down_size=float(row.down_size),  # type: ignore[arg-type]
                trade_count=int(row.trade_count),  # type: ignore[arg-type]
                bias_ratio=float(row.bias_ratio),  # type: ignore[arg-type]
                favoured_side=str(row.favoured_side),
                first_trade_ts=int(row.first_trade_ts),  # type: ignore[arg-type]
                last_trade_ts=int(row.last_trade_ts),  # type: ignore[arg-type]
            )
            for row in breakdowns_df.itertuples(index=False)
        ]

        signals, skip_counts = self._filter_breakdowns(breakdowns, now)

        if breakdowns:
            logger.info(
                "ANALYSE %d markets → %d signals"
                " (skipped: %d low-bias, %d non-BTC/ETH, %d no-window,"
                " %d expired, %d too-long, %d too-soon)",
                len(breakdowns),
                len(signals),
                skip_counts["bias"],
                skip_counts["asset"],
                skip_counts["window"],
                skip_counts["expired"],
                skip_counts["too_long"],
                skip_counts["too_soon"],
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

    async def _fetch_recent_trades(self) -> list[WhaleTrade]:
        """Fetch the whale's latest trades from the Polymarket Activity API.

        Use the ``/activity`` endpoint which is near-real-time (~10-30s
        delay) compared to ``/trades`` which lags by 3-5 minutes.
        Deduplicate using a composite key of ``hash:asset:size`` because
        one transaction can contain multiple fills.

        Returns:
            List of new ``WhaleTrade`` instances not seen before.

        """
        client = await self._get_client()
        now_ms = int(time.time() * _MS_PER_SECOND)

        try:
            resp = await client.get(
                f"{_DATA_API_BASE}/activity",
                params={
                    "user": self.whale_address,
                    "limit": _API_LIMIT,
                },
            )
            resp.raise_for_status()
        except Exception:
            logger.warning("Failed to fetch activity from Data API", exc_info=True)
            return []

        raw_trades: list[dict[str, Any]] = resp.json()
        new_trades: list[WhaleTrade] = []

        for raw in raw_trades:
            # Activity endpoint includes non-trade types (e.g. REDEEM)
            if raw.get("type") != "TRADE":
                continue
            tx_hash = str(raw.get("transactionHash", ""))
            asset = str(raw.get("asset", raw.get("asset_id", "")))
            size = str(raw.get("size", ""))
            dedup_key = f"{tx_hash}:{asset}:{size}"
            if not tx_hash or dedup_key in self._seen_hashes:
                continue
            self._seen_hashes.add(dedup_key)
            trade = _parse_trade(raw, self.whale_address, now_ms)
            if trade is not None:
                new_trades.append(trade)

        return new_trades

    def _filter_breakdowns(
        self,
        breakdowns: list[MarketBreakdown],
        now: int,
    ) -> tuple[list[CopySignal], dict[str, int]]:
        """Filter market breakdowns into actionable copy signals.

        Apply threshold filters (bias, asset, time window, expiry,
        min_time_to_start) and return qualifying signals with skip counts.

        Args:
            breakdowns: Per-market bias analysis results.
            now: Current UTC epoch seconds.

        Returns:
            Tuple of (signals, skip_counts dict).

        """
        signals: list[CopySignal] = []
        skips = {"bias": 0, "asset": 0, "window": 0, "expired": 0, "too_long": 0, "too_soon": 0}

        for bd in breakdowns:
            if bd.bias_ratio < float(self.min_bias):
                skips["bias"] += 1
                continue

            asset = parse_asset(bd.title)
            if asset is None:
                skips["asset"] += 1
                continue

            window = parse_time_window(bd.title, bd.first_trade_ts)
            if window is None:
                skips["window"] += 1
                continue

            start_ts, end_ts = window
            if end_ts <= now:
                skips["expired"] += 1
                continue

            if self.max_window_seconds > 0 and end_ts - start_ts > self.max_window_seconds:
                skips["too_long"] += 1
                continue

            if self.min_time_to_start > 0 and 0 < start_ts - now < self.min_time_to_start:
                skips["too_soon"] += 1
                continue

            total_vol = bd.up_volume + bd.down_volume
            up_pct = Decimal(str(bd.up_volume / total_vol)) if total_vol > 0 else Decimal("0.5")
            down_pct = Decimal(1) - up_pct

            signals.append(
                CopySignal(
                    condition_id=bd.condition_id,
                    title=bd.title,
                    asset=asset,
                    favoured_side=bd.favoured_side,
                    bias_ratio=Decimal(str(bd.bias_ratio)),
                    trade_count=bd.trade_count,
                    window_start_ts=start_ts,
                    window_end_ts=end_ts,
                    detected_at=now,
                    up_volume_pct=up_pct,
                    down_volume_pct=down_pct,
                )
            )

        return signals, skips

    @property
    def window_size(self) -> int:
        """Return the number of trades in the current rolling window."""
        return len(self._trades)


def _parse_trade(
    raw: dict[str, Any],
    address: str,
    collected_at_ms: int,
) -> WhaleTrade | None:
    """Parse a raw API trade dict into a WhaleTrade instance.

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
