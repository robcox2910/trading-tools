"""Tests for the SnapshotSimulator that converts candles to market snapshots."""

from datetime import datetime
from decimal import Decimal

import pytest

from trading_tools.apps.polymarket_bot.snapshot_simulator import SnapshotSimulator
from trading_tools.core.models import Candle, Interval

_SYMBOL = "BTC-USD"
_WINDOW_TS = 1_708_300_800  # arbitrary aligned timestamp
_HALF = Decimal("0.5")
_SCALE = Decimal(15)


def _make_candle(
    timestamp: int,
    open_: str = "100",
    close: str = "100",
    *,
    high: str | None = None,
    low: str | None = None,
) -> Candle:
    """Create a 1-minute candle with sensible defaults.

    Args:
        timestamp: Unix epoch seconds.
        open_: Open price as string.
        close: Close price as string.
        high: High price (defaults to max of open/close).
        low: Low price (defaults to min of open/close).

    Returns:
        Candle instance for testing.

    """
    o = Decimal(open_)
    c = Decimal(close)
    h = Decimal(high) if high else max(o, c)
    l_ = Decimal(low) if low else min(o, c)
    return Candle(
        symbol=_SYMBOL,
        timestamp=timestamp,
        open=o,
        high=h,
        low=l_,
        close=c,
        volume=Decimal(1000),
        interval=Interval.M1,
    )


class TestSnapshotSimulatorInit:
    """Test SnapshotSimulator initialization and validation."""

    def test_default_scale_factor(self) -> None:
        """Verify default scale factor is 15."""
        sim = SnapshotSimulator()
        # Access via simulate to confirm it works
        candle = _make_candle(_WINDOW_TS + 60)
        snapshots = sim.simulate_window(_SYMBOL, _WINDOW_TS, [candle])
        assert len(snapshots) == 1

    def test_custom_scale_factor(self) -> None:
        """Accept a custom positive scale factor."""
        sim = SnapshotSimulator(scale_factor=Decimal(30))
        candle = _make_candle(_WINDOW_TS + 60)
        snapshots = sim.simulate_window(_SYMBOL, _WINDOW_TS, [candle])
        assert len(snapshots) == 1

    def test_zero_scale_factor_raises(self) -> None:
        """Reject zero scale factor."""
        with pytest.raises(ValueError, match="positive"):
            SnapshotSimulator(scale_factor=Decimal(0))

    def test_negative_scale_factor_raises(self) -> None:
        """Reject negative scale factor."""
        with pytest.raises(ValueError, match="positive"):
            SnapshotSimulator(scale_factor=Decimal(-5))


class TestSimulateWindow:
    """Test the simulate_window method for correct snapshot generation."""

    def test_empty_candles_raises(self) -> None:
        """Reject empty candles list."""
        sim = SnapshotSimulator()
        with pytest.raises(ValueError, match="empty"):
            sim.simulate_window(_SYMBOL, _WINDOW_TS, [])

    def test_single_flat_candle_returns_near_half(self) -> None:
        """A flat candle (open == close) produces YES ≈ 0.50."""
        sim = SnapshotSimulator()
        candle = _make_candle(_WINDOW_TS + 60, open_="100", close="100")
        snapshots = sim.simulate_window(_SYMBOL, _WINDOW_TS, [candle])

        assert len(snapshots) == 1
        assert snapshots[0].yes_price == _HALF
        assert snapshots[0].no_price == _HALF

    def test_upward_movement_increases_yes_price(self) -> None:
        """An upward price move pushes YES above 0.50."""
        sim = SnapshotSimulator()
        candle = _make_candle(
            _WINDOW_TS + 60,
            open_="100",
            close="102",
            high="102",
        )
        snapshots = sim.simulate_window(_SYMBOL, _WINDOW_TS, [candle])

        assert snapshots[0].yes_price > _HALF
        assert snapshots[0].no_price < _HALF

    def test_downward_movement_decreases_yes_price(self) -> None:
        """A downward price move pushes YES below 0.50."""
        sim = SnapshotSimulator()
        candle = _make_candle(
            _WINDOW_TS + 60,
            open_="100",
            close="98",
            low="98",
        )
        snapshots = sim.simulate_window(_SYMBOL, _WINDOW_TS, [candle])

        assert snapshots[0].yes_price < _HALF
        assert snapshots[0].no_price > _HALF

    def test_five_candles_converge_toward_certainty(self) -> None:
        """Five candles with consistent upward trend converge toward 0.995."""
        sim = SnapshotSimulator(scale_factor=_SCALE)
        candles = [
            _make_candle(_WINDOW_TS + 60, open_="100", close="100.5", high="100.5"),
            _make_candle(_WINDOW_TS + 120, open_="100.5", close="101", high="101"),
            _make_candle(_WINDOW_TS + 180, open_="101", close="101.5", high="101.5"),
            _make_candle(_WINDOW_TS + 240, open_="101.5", close="102", high="102"),
            _make_candle(_WINDOW_TS + 300, open_="102", close="102.5", high="102.5"),
        ]
        snapshots = sim.simulate_window(_SYMBOL, _WINDOW_TS, candles)

        expected_count = 5
        assert len(snapshots) == expected_count
        # Each subsequent snapshot should have higher YES
        for i in range(1, len(snapshots)):
            assert snapshots[i].yes_price >= snapshots[i - 1].yes_price
        # Final snapshot should be well above midpoint
        min_final_yes = Decimal("0.85")
        assert snapshots[-1].yes_price > min_final_yes

    def test_yes_no_prices_sum_to_one(self) -> None:
        """YES + NO should always equal 1.0."""
        sim = SnapshotSimulator()
        candles = [
            _make_candle(_WINDOW_TS + 60, open_="100", close="101", high="101"),
            _make_candle(_WINDOW_TS + 120, open_="101", close="99", low="99"),
            _make_candle(_WINDOW_TS + 180, open_="99", close="103", high="103"),
        ]
        snapshots = sim.simulate_window(_SYMBOL, _WINDOW_TS, candles)

        for snap in snapshots:
            assert snap.yes_price + snap.no_price == Decimal(1)

    def test_condition_id_format(self) -> None:
        """Condition ID follows the symbol_timestamp format."""
        sim = SnapshotSimulator()
        candle = _make_candle(_WINDOW_TS + 60)
        snapshots = sim.simulate_window(_SYMBOL, _WINDOW_TS, [candle])

        expected_id = f"{_SYMBOL}_{_WINDOW_TS}"
        assert snapshots[0].condition_id == expected_id

    def test_end_date_is_window_plus_300(self) -> None:
        """End date is ISO-8601 representation of window_open + 300s."""
        sim = SnapshotSimulator()
        candle = _make_candle(_WINDOW_TS + 60)
        snapshots = sim.simulate_window(_SYMBOL, _WINDOW_TS, [candle])

        end_date = snapshots[0].end_date
        assert end_date  # non-empty
        # Should be parseable ISO-8601
        parsed = datetime.fromisoformat(end_date)
        assert parsed.timestamp() == _WINDOW_TS + 300

    def test_prices_clamped_within_valid_range(self) -> None:
        """Even with extreme moves, prices stay in [0.005, 0.995]."""
        sim = SnapshotSimulator(scale_factor=Decimal(100))
        candle = _make_candle(
            _WINDOW_TS + 300,
            open_="100",
            close="200",
            high="200",
        )
        snapshots = sim.simulate_window(_SYMBOL, _WINDOW_TS, [candle])

        max_yes = Decimal("0.995")
        min_yes = Decimal("0.005")
        assert snapshots[0].yes_price <= max_yes
        assert snapshots[0].yes_price >= min_yes

    def test_timestamp_matches_candle(self) -> None:
        """Snapshot timestamp comes from the candle, not the window."""
        sim = SnapshotSimulator()
        candle_ts = _WINDOW_TS + 120
        candle = _make_candle(candle_ts)
        snapshots = sim.simulate_window(_SYMBOL, _WINDOW_TS, [candle])

        assert snapshots[0].timestamp == candle_ts

    def test_higher_scale_produces_more_extreme_prices(self) -> None:
        """Higher scale factor produces more extreme YES prices."""
        candle = _make_candle(
            _WINDOW_TS + 60,
            open_="100",
            close="101",
            high="101",
        )
        low_scale = SnapshotSimulator(scale_factor=Decimal(5))
        high_scale = SnapshotSimulator(scale_factor=Decimal(50))

        low_snaps = low_scale.simulate_window(_SYMBOL, _WINDOW_TS, [candle])
        high_snaps = high_scale.simulate_window(_SYMBOL, _WINDOW_TS, [candle])

        assert high_snaps[0].yes_price > low_snaps[0].yes_price
