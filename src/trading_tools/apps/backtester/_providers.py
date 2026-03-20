"""Reusable candle providers for backtester internals.

Provide a ``CachedProvider`` that wraps a pre-fetched list of candles so
that multiple consumers (e.g. strategy comparison, walk-forward windows)
can share the same data without redundant I/O.
"""

from trading_tools.core.models import Candle, Interval


class CachedProvider:
    """Candle provider that returns pre-fetched candles.

    Wrap a list of candles already retrieved from the real provider so
    that each concurrent consumer reuses the same data without making
    redundant network or I/O calls.
    """

    def __init__(self, candles: list[Candle]) -> None:
        """Initialize with a fixed list of candles.

        Args:
            candles: Pre-fetched candle data.

        """
        self._candles = candles

    async def get_candles(
        self,
        symbol: str,  # noqa: ARG002
        interval: Interval,  # noqa: ARG002
        start_ts: int,  # noqa: ARG002
        end_ts: int,  # noqa: ARG002
    ) -> list[Candle]:
        """Return the pre-fetched candles ignoring filter parameters.

        Args:
            symbol: Ignored — included to satisfy ``CandleProvider`` protocol.
            interval: Ignored.
            start_ts: Ignored.
            end_ts: Ignored.

        Returns:
            The full list of pre-fetched candles.

        """
        return self._candles
