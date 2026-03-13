"""Configuration dataclass for the whale trade monitor service.

Hold all tuneable parameters for the monitor: database URL, poll interval,
and initial whale addresses. Immutable after construction to prevent
accidental mutation during long-running collection sessions.
"""

from dataclasses import dataclass

_DEFAULT_POLL_INTERVAL = 120
_DEFAULT_API_LIMIT = 1000
_DEFAULT_MAX_OFFSET = 3000


@dataclass(frozen=True)
class WhaleMonitorConfig:
    """Immutable configuration for a whale monitor session.

    Attributes:
        db_url: SQLAlchemy async connection string
            (e.g. ``sqlite+aiosqlite:///whale_data.db``).
        whales: Initial whale addresses to track. Additional whales
            can be pre-loaded in the database.
        poll_interval_seconds: How often to poll each whale's trades,
            in seconds.
        api_limit: Maximum records per API page request.
        max_offset: Maximum pagination offset (API caps at 3000 for
            a total of 4000 records with limit=1000).

    """

    db_url: str
    whales: tuple[str, ...] = ()
    poll_interval_seconds: int = _DEFAULT_POLL_INTERVAL
    api_limit: int = _DEFAULT_API_LIMIT
    max_offset: int = _DEFAULT_MAX_OFFSET
