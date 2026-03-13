"""Tests for whale monitor ORM models."""

from trading_tools.apps.whale_monitor.models import Base, TrackedWhale, WhaleTrade

_SAMPLE_ADDRESS = "0xa45fe11dd1420fca906ceac2c067844379a42429"
_SAMPLE_LABEL = "Wry-Leaker"
_SAMPLE_TX_HASH = "0xabc123def456"
_SAMPLE_ASSET_ID = "asset_abc123"
_SAMPLE_CONDITION_ID = "cond_xyz789"
_SAMPLE_PRICE = 0.72
_SAMPLE_SIZE = 50.0
_SAMPLE_TIMESTAMP = 1700000000
_SAMPLE_ADDED_AT = 1700000000
_SAMPLE_COLLECTED_AT = 1700000000000
_SAMPLE_OUTCOME_INDEX = 0


class TestTrackedWhaleModel:
    """Tests for the TrackedWhale SQLAlchemy model."""

    def test_tracked_whale_creation(self) -> None:
        """Create a TrackedWhale instance with all fields populated."""
        whale = TrackedWhale(
            address=_SAMPLE_ADDRESS,
            label=_SAMPLE_LABEL,
            added_at=_SAMPLE_ADDED_AT,
            active=True,
        )

        assert whale.address == _SAMPLE_ADDRESS
        assert whale.label == _SAMPLE_LABEL
        assert whale.added_at == _SAMPLE_ADDED_AT
        assert whale.active is True

    def test_tracked_whale_inactive(self) -> None:
        """Create a TrackedWhale with active=False."""
        whale = TrackedWhale(
            address=_SAMPLE_ADDRESS,
            label=_SAMPLE_LABEL,
            added_at=_SAMPLE_ADDED_AT,
            active=False,
        )

        assert whale.active is False

    def test_tracked_whale_tablename(self) -> None:
        """Verify the table name is 'tracked_whales'."""
        assert TrackedWhale.__tablename__ == "tracked_whales"

    def test_base_metadata_contains_tracked_whales_table(self) -> None:
        """Verify the Base metadata includes the tracked_whales table."""
        assert "tracked_whales" in Base.metadata.tables


class TestWhaleTradeModel:
    """Tests for the WhaleTrade SQLAlchemy model."""

    def test_whale_trade_creation(self) -> None:
        """Create a WhaleTrade instance with all fields populated."""
        trade = WhaleTrade(
            whale_address=_SAMPLE_ADDRESS,
            transaction_hash=_SAMPLE_TX_HASH,
            side="BUY",
            asset_id=_SAMPLE_ASSET_ID,
            condition_id=_SAMPLE_CONDITION_ID,
            size=_SAMPLE_SIZE,
            price=_SAMPLE_PRICE,
            timestamp=_SAMPLE_TIMESTAMP,
            title="BTC Up/Down",
            slug="btc-updown",
            outcome="Up",
            outcome_index=_SAMPLE_OUTCOME_INDEX,
            collected_at=_SAMPLE_COLLECTED_AT,
        )

        assert trade.whale_address == _SAMPLE_ADDRESS
        assert trade.transaction_hash == _SAMPLE_TX_HASH
        assert trade.side == "BUY"
        assert trade.asset_id == _SAMPLE_ASSET_ID
        assert trade.condition_id == _SAMPLE_CONDITION_ID
        assert trade.size == _SAMPLE_SIZE
        assert trade.price == _SAMPLE_PRICE
        assert trade.timestamp == _SAMPLE_TIMESTAMP
        assert trade.title == "BTC Up/Down"
        assert trade.slug == "btc-updown"
        assert trade.outcome == "Up"
        assert trade.outcome_index == _SAMPLE_OUTCOME_INDEX
        assert trade.collected_at == _SAMPLE_COLLECTED_AT

    def test_whale_trade_sell_side(self) -> None:
        """Create a WhaleTrade with SELL side."""
        trade = WhaleTrade(
            whale_address=_SAMPLE_ADDRESS,
            transaction_hash=_SAMPLE_TX_HASH,
            side="SELL",
            asset_id=_SAMPLE_ASSET_ID,
            condition_id=_SAMPLE_CONDITION_ID,
            size=_SAMPLE_SIZE,
            price=_SAMPLE_PRICE,
            timestamp=_SAMPLE_TIMESTAMP,
            title="BTC Up/Down",
            slug="btc-updown",
            outcome="Down",
            outcome_index=1,
            collected_at=_SAMPLE_COLLECTED_AT,
        )

        assert trade.side == "SELL"

    def test_whale_trade_tablename(self) -> None:
        """Verify the table name is 'whale_trades'."""
        assert WhaleTrade.__tablename__ == "whale_trades"

    def test_base_metadata_contains_whale_trades_table(self) -> None:
        """Verify the Base metadata includes the whale_trades table."""
        assert "whale_trades" in Base.metadata.tables

    def test_composite_index_address_timestamp(self) -> None:
        """Verify the composite index on (whale_address, timestamp) is defined."""
        table = Base.metadata.tables["whale_trades"]
        index_names = {idx.name for idx in table.indexes}
        assert "ix_whale_trades_address_timestamp" in index_names

    def test_composite_index_condition_timestamp(self) -> None:
        """Verify the composite index on (condition_id, timestamp) is defined."""
        table = Base.metadata.tables["whale_trades"]
        index_names = {idx.name for idx in table.indexes}
        assert "ix_whale_trades_condition_timestamp" in index_names
