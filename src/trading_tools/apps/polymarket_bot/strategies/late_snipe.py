"""Late snipe strategy for short-duration prediction markets.

Buy whichever outcome token crosses a price threshold in the final window
before market resolution. Designed for 5-minute "Up or Down" crypto markets
where one side typically reaches high confidence in the closing seconds.
"""

import logging
from datetime import UTC, datetime
from decimal import Decimal

from trading_tools.apps.polymarket_bot.models import MarketSnapshot
from trading_tools.core.models import ONE, Side, Signal

_DEFAULT_THRESHOLD = Decimal("0.80")
_DEFAULT_WINDOW_SECONDS = 60


class PMLateSnipeStrategy:
    """Snipe the likely winner in the final window of a prediction market.

    Monitor the market and generate a BUY signal when either the YES or NO
    price crosses the configured threshold within the last ``window_seconds``
    before the market's ``end_date``. The signal strength reflects how far
    past the threshold the price has moved.

    This strategy is designed for binary markets with short durations
    (e.g. 5-minute crypto Up/Down markets) where one side typically
    reaches high conviction near expiry.
    """

    def __init__(
        self,
        threshold: Decimal = _DEFAULT_THRESHOLD,
        window_seconds: int = _DEFAULT_WINDOW_SECONDS,
    ) -> None:
        """Initialize the late snipe strategy.

        Args:
            threshold: Minimum price (0.5-1.0) to trigger a buy signal.
            window_seconds: Seconds before market end to start watching.

        Raises:
            ValueError: If threshold is not in (0.5, 1.0) or window_seconds < 1.

        """
        if not (Decimal("0.5") < threshold < ONE):
            msg = f"threshold must be in (0.5, 1.0), got {threshold}"
            raise ValueError(msg)
        if window_seconds < 1:
            msg = f"window_seconds must be >= 1, got {window_seconds}"
            raise ValueError(msg)
        self._threshold = threshold
        self._window_seconds = window_seconds
        self._bought: set[str] = set()

    @property
    def name(self) -> str:
        """Return the strategy name including parameters."""
        return f"pm_late_snipe_{self._threshold}_{self._window_seconds}s"

    def on_snapshot(
        self,
        snapshot: MarketSnapshot,
        history: list[MarketSnapshot],  # noqa: ARG002
        related: list[MarketSnapshot] | None = None,  # noqa: ARG002
    ) -> Signal | None:
        """Evaluate whether to snipe a market in its final window.

        Parse the snapshot's ``end_date`` to determine seconds remaining.
        If within the window and either side exceeds the threshold, signal
        BUY. Only signal once per market (tracked by condition_id).

        Args:
            snapshot: Current market state.
            history: Previous snapshots (unused).
            related: Related market snapshots (unused).

        Returns:
            A ``Signal`` if a snipe opportunity is detected, else ``None``.

        """
        if snapshot.condition_id in self._bought:
            return None

        seconds_remaining = self._seconds_until_end(snapshot)
        if seconds_remaining is None or seconds_remaining > self._window_seconds:
            return None

        # Check if either side exceeds threshold
        if snapshot.yes_price >= self._threshold:
            self._bought.add(snapshot.condition_id)
            return Signal(
                side=Side.BUY,
                symbol=snapshot.condition_id,
                strength=min(snapshot.yes_price, ONE),
                reason=(
                    f"Late snipe YES at {snapshot.yes_price:.4f} "
                    f"(>= {self._threshold}), {seconds_remaining:.0f}s remaining"
                ),
            )

        if snapshot.no_price >= self._threshold:
            self._bought.add(snapshot.condition_id)
            return Signal(
                side=Side.SELL,
                symbol=snapshot.condition_id,
                strength=min(snapshot.no_price, ONE),
                reason=(
                    f"Late snipe NO at {snapshot.no_price:.4f} "
                    f"(>= {self._threshold}), {seconds_remaining:.0f}s remaining"
                ),
            )

        return None

    @staticmethod
    def _seconds_until_end(snapshot: MarketSnapshot) -> float | None:
        """Calculate seconds remaining until market resolution.

        Args:
            snapshot: Market snapshot with end_date and timestamp.

        Returns:
            Seconds remaining, or ``None`` if end_date cannot be parsed.

        """
        end_str = snapshot.end_date
        if not end_str:
            return None
        try:
            end_dt = datetime.fromisoformat(end_str)
        except (ValueError, TypeError):
            logging.getLogger(__name__).warning(
                "Cannot parse end_date %r for market %s",
                end_str,
                snapshot.condition_id[:20],
            )
            return None

        now_dt = datetime.fromtimestamp(snapshot.timestamp, tz=UTC)
        remaining = (end_dt - now_dt).total_seconds()
        return max(remaining, 0.0)
