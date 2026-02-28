"""Configuration dataclass for the tick collector service.

Hold all tuneable parameters for the collector: database URL, market selection,
discovery intervals, and flush behaviour. Immutable after construction to
prevent accidental mutation during long-running collection sessions.
"""

from dataclasses import dataclass

_DEFAULT_DISCOVERY_INTERVAL = 300
_DEFAULT_FLUSH_INTERVAL = 10
_DEFAULT_FLUSH_BATCH_SIZE = 100
_DEFAULT_RECONNECT_BASE_DELAY = 5.0


@dataclass(frozen=True)
class CollectorConfig:
    """Immutable configuration for a tick collector session.

    Attributes:
        db_url: SQLAlchemy async connection string
            (e.g. ``sqlite+aiosqlite:///ticks.db``).
        markets: Static condition IDs to subscribe to.
        series_slugs: Gamma API series slugs for auto-discovery
            (e.g. ``("btc-updown-5m",)``).
        discovery_interval_seconds: How often to re-discover markets from
            series slugs, in seconds.
        flush_interval_seconds: Maximum time between database writes, in
            seconds. The buffer is flushed when this timer fires even if
            the batch is not full.
        flush_batch_size: Maximum ticks buffered before a forced flush.
        reconnect_base_delay: Initial delay in seconds for exponential
            backoff on WebSocket reconnection.

    """

    db_url: str
    markets: tuple[str, ...] = ()
    series_slugs: tuple[str, ...] = ()
    discovery_interval_seconds: int = _DEFAULT_DISCOVERY_INTERVAL
    flush_interval_seconds: int = _DEFAULT_FLUSH_INTERVAL
    flush_batch_size: int = _DEFAULT_FLUSH_BATCH_SIZE
    reconnect_base_delay: float = _DEFAULT_RECONNECT_BASE_DELAY
