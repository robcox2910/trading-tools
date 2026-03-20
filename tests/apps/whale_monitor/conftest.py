"""Shared test fixtures and factories for whale_monitor tests.

Provide reusable helpers to create WhaleTrade, TrackedWhale, and
WhaleMonitorConfig objects used across collector, analyser, and
signal_detector test modules.
"""

from __future__ import annotations

from typing import Any

from trading_tools.apps.whale_monitor.config import WhaleMonitorConfig
from trading_tools.apps.whale_monitor.models import TrackedWhale, WhaleTrade

DEFAULT_ADDRESS = "0xa45fe11dd1420fca906ceac2c067844379a42429"
DEFAULT_COLLECTED_AT = 1700000000000
DEFAULT_TIMESTAMP = 1700000000


def make_whale_trade(
    whale_address: str = DEFAULT_ADDRESS,
    side: str = "BUY",
    size: float = 50.0,
    price: float = 0.72,
    condition_id: str = "cond_a",
    title: str = "BTC Up/Down",
    outcome: str = "Up",
    outcome_index: int = 0,
    timestamp: int = DEFAULT_TIMESTAMP,
    tx_hash: str = "tx_001",
    asset_id: str = "asset_test",
    slug: str = "test",
    collected_at: int = DEFAULT_COLLECTED_AT,
) -> WhaleTrade:
    """Create a WhaleTrade instance for testing.

    Args:
        whale_address: Whale wallet address.
        side: Trade direction (BUY or SELL).
        size: Token quantity.
        price: Execution price.
        condition_id: Market condition ID.
        title: Market title.
        outcome: Outcome label (e.g. "Up", "Down").
        outcome_index: Numeric outcome index.
        timestamp: Epoch seconds.
        tx_hash: Transaction hash.
        asset_id: Asset identifier.
        slug: Market slug.
        collected_at: Collection timestamp in milliseconds.

    Returns:
        A WhaleTrade instance populated with test data.

    """
    return WhaleTrade(
        whale_address=whale_address,
        transaction_hash=tx_hash,
        side=side,
        asset_id=asset_id,
        condition_id=condition_id,
        size=size,
        price=price,
        timestamp=timestamp,
        title=title,
        slug=slug,
        outcome=outcome,
        outcome_index=outcome_index,
        collected_at=collected_at,
    )


def make_raw_trade(
    tx_hash: str = "0xabc123",
    price: float = 0.72,
    size: float = 50.0,
) -> dict[str, Any]:
    """Create a sample raw trade dict matching the Data API format.

    Args:
        tx_hash: Transaction hash.
        price: Trade price.
        size: Trade size.

    Returns:
        Trade dictionary matching Polymarket Data API format.

    """
    return {
        "transactionHash": tx_hash,
        "side": "BUY",
        "asset_id": "asset_test",
        "condition_id": "cond_test",
        "size": size,
        "price": price,
        "timestamp": DEFAULT_TIMESTAMP,
        "market": "BTC Up/Down",
        "slug": "btc-updown",
        "outcome": "Up",
        "outcome_index": 0,
    }


def make_tracked_whale(
    address: str = DEFAULT_ADDRESS,
    label: str = "Test-Whale",
) -> TrackedWhale:
    """Create a TrackedWhale instance for testing.

    Args:
        address: Whale proxy wallet address.
        label: Friendly name.

    Returns:
        A TrackedWhale instance.

    """
    return TrackedWhale(
        address=address,
        label=label,
        added_at=DEFAULT_TIMESTAMP,
        active=True,
    )


def make_whale_config(
    *,
    db_url: str = "sqlite+aiosqlite:///:memory:",
    whales: tuple[str, ...] = (DEFAULT_ADDRESS,),
    poll_interval_seconds: int = 60,
) -> WhaleMonitorConfig:
    """Create a WhaleMonitorConfig for testing.

    Args:
        db_url: Database connection string.
        whales: Initial whale addresses.
        poll_interval_seconds: Polling interval.

    Returns:
        WhaleMonitorConfig with test parameters.

    """
    return WhaleMonitorConfig(
        db_url=db_url,
        whales=whales,
        poll_interval_seconds=poll_interval_seconds,
    )
