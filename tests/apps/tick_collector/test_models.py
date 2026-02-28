"""Tests for tick collector ORM models."""

from trading_tools.apps.tick_collector.models import Base, Tick

_SAMPLE_ASSET_ID = "asset_abc123"
_SAMPLE_CONDITION_ID = "cond_xyz789"
_SAMPLE_PRICE = 0.72
_SAMPLE_SIZE = 15.5
_SAMPLE_FEE_BPS = 200
_SAMPLE_TIMESTAMP = 1700000000000
_SAMPLE_RECEIVED_AT = 1700000000050


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
