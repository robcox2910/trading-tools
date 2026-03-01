"""Tests for tick collector ORM models."""

from trading_tools.apps.tick_collector.models import Base, OrderBookSnapshot, Tick

_SAMPLE_ASSET_ID = "asset_abc123"
_SAMPLE_CONDITION_ID = "cond_xyz789"
_SAMPLE_TOKEN_ID = "token_abc123"
_SAMPLE_PRICE = 0.72
_SAMPLE_SIZE = 15.5
_SAMPLE_FEE_BPS = 200
_SAMPLE_TIMESTAMP = 1700000000000
_SAMPLE_RECEIVED_AT = 1700000000050
_SAMPLE_SPREAD = 0.02
_SAMPLE_MIDPOINT = 0.73
_SAMPLE_BIDS_JSON = '[["0.72", "100"], ["0.71", "200"]]'
_SAMPLE_ASKS_JSON = '[["0.74", "150"], ["0.75", "250"]]'


class TestTickModel:
    """Tests for the Tick SQLAlchemy model."""

    def test_tick_creation(self) -> None:
        """Create a Tick instance with all fields populated."""
        tick = Tick(
            asset_id=_SAMPLE_ASSET_ID,
            condition_id=_SAMPLE_CONDITION_ID,
            price=_SAMPLE_PRICE,
            size=_SAMPLE_SIZE,
            side="BUY",
            fee_rate_bps=_SAMPLE_FEE_BPS,
            timestamp=_SAMPLE_TIMESTAMP,
            received_at=_SAMPLE_RECEIVED_AT,
        )

        assert tick.asset_id == _SAMPLE_ASSET_ID
        assert tick.condition_id == _SAMPLE_CONDITION_ID
        assert tick.price == _SAMPLE_PRICE
        assert tick.size == _SAMPLE_SIZE
        assert tick.side == "BUY"
        assert tick.fee_rate_bps == _SAMPLE_FEE_BPS
        assert tick.timestamp == _SAMPLE_TIMESTAMP
        assert tick.received_at == _SAMPLE_RECEIVED_AT

    def test_tick_sell_side(self) -> None:
        """Create a Tick with SELL side."""
        tick = Tick(
            asset_id=_SAMPLE_ASSET_ID,
            condition_id=_SAMPLE_CONDITION_ID,
            price=_SAMPLE_PRICE,
            size=_SAMPLE_SIZE,
            side="SELL",
            fee_rate_bps=_SAMPLE_FEE_BPS,
            timestamp=_SAMPLE_TIMESTAMP,
            received_at=_SAMPLE_RECEIVED_AT,
        )

        assert tick.side == "SELL"

    def test_tick_tablename(self) -> None:
        """Verify the table name is 'ticks'."""
        assert Tick.__tablename__ == "ticks"

    def test_base_metadata_contains_ticks_table(self) -> None:
        """Verify the Base metadata includes the ticks table."""
        assert "ticks" in Base.metadata.tables

    def test_composite_index_exists(self) -> None:
        """Verify the composite index on (asset_id, timestamp) is defined."""
        table = Base.metadata.tables["ticks"]
        index_names = {idx.name for idx in table.indexes}
        assert "ix_ticks_asset_timestamp" in index_names


class TestOrderBookSnapshotModel:
    """Tests for the OrderBookSnapshot SQLAlchemy model."""

    def test_order_book_snapshot_fields(self) -> None:
        """Create an OrderBookSnapshot and verify all fields are stored."""
        snapshot = OrderBookSnapshot(
            token_id=_SAMPLE_TOKEN_ID,
            timestamp=_SAMPLE_TIMESTAMP,
            bids_json=_SAMPLE_BIDS_JSON,
            asks_json=_SAMPLE_ASKS_JSON,
            spread=_SAMPLE_SPREAD,
            midpoint=_SAMPLE_MIDPOINT,
        )

        assert snapshot.token_id == _SAMPLE_TOKEN_ID
        assert snapshot.timestamp == _SAMPLE_TIMESTAMP
        assert snapshot.bids_json == _SAMPLE_BIDS_JSON
        assert snapshot.asks_json == _SAMPLE_ASKS_JSON
        assert snapshot.spread == _SAMPLE_SPREAD
        assert snapshot.midpoint == _SAMPLE_MIDPOINT

    def test_order_book_snapshot_tablename(self) -> None:
        """Verify the table name is 'order_book_snapshots'."""
        assert OrderBookSnapshot.__tablename__ == "order_book_snapshots"

    def test_base_metadata_contains_order_book_snapshots_table(self) -> None:
        """Verify the Base metadata includes the order_book_snapshots table."""
        assert "order_book_snapshots" in Base.metadata.tables

    def test_composite_index_on_token_timestamp(self) -> None:
        """Verify the composite index on (token_id, timestamp) is defined."""
        table = Base.metadata.tables["order_book_snapshots"]
        index_names = {idx.name for idx in table.indexes}
        assert "ix_book_token_timestamp" in index_names
