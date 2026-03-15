"""Tests for the SignalDetector class."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from trading_tools.apps.whale_copy_trader.signal_detector import SignalDetector
from trading_tools.apps.whale_monitor.correlator import parse_time_window
from trading_tools.apps.whale_monitor.models import WhaleTrade

_ADDRESS = "0xwhale"
_COLLECTED_AT = 1700000000000
_SECONDS_BEFORE_START = 30
_EXPECTED_TRADE_COUNT = 4

# Use a timestamp far in the future to ensure windows are "in the future"
_FUTURE_TS = 4_000_000_000
_PAST_TS = 1_000_000_000


def _make_trade(
    outcome: str = "Up",
    size: float = 50.0,
    price: float = 0.72,
    condition_id: str = "cond_a",
    tx_hash: str = "tx_001",
    title: str = "Bitcoin Up or Down - March 13, 6PM ET",
    timestamp: int = _FUTURE_TS,
) -> WhaleTrade:
    """Create a WhaleTrade instance for testing.

    Args:
        outcome: Outcome label.
        size: Token quantity.
        price: Execution price.
        condition_id: Market condition ID.
        tx_hash: Transaction hash.
        title: Market title.
        timestamp: Epoch seconds.

    Returns:
        A WhaleTrade instance.

    """
    return WhaleTrade(
        whale_address=_ADDRESS,
        transaction_hash=tx_hash,
        side="BUY",
        asset_id="asset_test",
        condition_id=condition_id,
        size=size,
        price=price,
        timestamp=timestamp,
        title=title,
        slug="btc-up-down",
        outcome=outcome,
        outcome_index=0,
        collected_at=_COLLECTED_AT,
    )


class TestSignalDetector:
    """Tests for signal detection from whale trades via direct API polling."""

    @pytest.fixture
    def detector(self) -> SignalDetector:
        """Create a SignalDetector with default config."""
        return SignalDetector(
            whale_address=_ADDRESS,
            min_bias=Decimal("1.5"),
            min_trades=3,
            lookback_seconds=300,
            min_time_to_start=60,
        )

    @pytest.mark.asyncio
    async def test_no_trades_returns_empty(self, detector: SignalDetector) -> None:
        """Return empty list when no trades exist."""
        with patch.object(
            detector, "_fetch_recent_trades", new_callable=AsyncMock, return_value=[]
        ):
            signals = await detector.detect_signals()
        assert signals == []

    @pytest.mark.asyncio
    async def test_filters_by_asset(self, detector: SignalDetector) -> None:
        """Skip markets that are not BTC or ETH."""
        trades = [
            _make_trade(
                title="Solana Up or Down - March 13, 6PM ET",
                outcome="Up",
                tx_hash=f"tx_{i}",
            )
            for i in range(5)
        ]
        with patch.object(
            detector, "_fetch_recent_trades", new_callable=AsyncMock, return_value=trades
        ):
            signals = await detector.detect_signals()
        assert signals == []

    @pytest.mark.asyncio
    async def test_filters_by_min_trades(self, detector: SignalDetector) -> None:
        """Skip markets with fewer trades than min_trades threshold."""
        trades = [_make_trade(outcome="Up", tx_hash="tx_1")]
        with patch.object(
            detector, "_fetch_recent_trades", new_callable=AsyncMock, return_value=trades
        ):
            signals = await detector.detect_signals()
        assert signals == []

    @pytest.mark.asyncio
    async def test_filters_by_bias_ratio(self, detector: SignalDetector) -> None:
        """Skip markets whose bias ratio is below min_bias."""
        trades = [
            _make_trade(outcome="Up", size=50.0, price=0.60, tx_hash="tx_1"),
            _make_trade(outcome="Up", size=50.0, price=0.60, tx_hash="tx_2"),
            _make_trade(outcome="Down", size=50.0, price=0.60, tx_hash="tx_3"),
            _make_trade(outcome="Down", size=50.0, price=0.60, tx_hash="tx_4"),
        ]
        with patch.object(
            detector, "_fetch_recent_trades", new_callable=AsyncMock, return_value=trades
        ):
            signals = await detector.detect_signals()
        assert signals == []

    @pytest.mark.asyncio
    async def test_filters_expired_windows(self, detector: SignalDetector) -> None:
        """Skip markets whose time window has already passed."""
        trades = [
            _make_trade(
                outcome="Up",
                tx_hash=f"tx_{i}",
                title="Bitcoin Up or Down - January 1, 6PM ET",
                timestamp=_PAST_TS,
            )
            for i in range(5)
        ]
        with patch.object(
            detector, "_fetch_recent_trades", new_callable=AsyncMock, return_value=trades
        ):
            signals = await detector.detect_signals()
        assert signals == []

    @pytest.mark.asyncio
    async def test_detects_valid_signal(self, detector: SignalDetector) -> None:
        """Detect a valid signal when all thresholds are met."""
        trades = [
            _make_trade(outcome="Up", size=100.0, price=0.70, tx_hash="tx_1"),
            _make_trade(outcome="Up", size=100.0, price=0.70, tx_hash="tx_2"),
            _make_trade(outcome="Up", size=100.0, price=0.70, tx_hash="tx_3"),
            _make_trade(outcome="Down", size=10.0, price=0.30, tx_hash="tx_4"),
        ]
        with patch.object(
            detector, "_fetch_recent_trades", new_callable=AsyncMock, return_value=trades
        ):
            signals = await detector.detect_signals()

        assert len(signals) == 1
        sig = signals[0]
        assert sig.condition_id == "cond_a"
        assert sig.asset == "BTC-USD"
        assert sig.favoured_side == "Up"
        assert sig.bias_ratio > Decimal("1.5")
        assert sig.up_volume_pct > Decimal("0.9")
        assert sig.down_volume_pct < Decimal("0.1")
        assert (sig.up_volume_pct + sig.down_volume_pct).quantize(Decimal("0.01")) == Decimal(
            "1.00"
        )

    @pytest.mark.asyncio
    async def test_detects_eth_signal(self, detector: SignalDetector) -> None:
        """Detect an ETH signal from Ethereum market titles."""
        trades = [
            _make_trade(
                outcome="Down",
                size=100.0,
                price=0.70,
                tx_hash=f"tx_{i}",
                title="Ethereum Up or Down - March 13, 6PM ET",
            )
            for i in range(4)
        ]
        with patch.object(
            detector, "_fetch_recent_trades", new_callable=AsyncMock, return_value=trades
        ):
            signals = await detector.detect_signals()

        assert len(signals) == 1
        assert signals[0].asset == "ETH-USD"
        assert signals[0].favoured_side == "Down"

    @pytest.mark.asyncio
    async def test_deduplicates_by_tx_hash(self, detector: SignalDetector) -> None:
        """Do not re-process trades already seen in previous polls."""
        trades = [
            _make_trade(outcome="Up", size=100.0, price=0.70, tx_hash=f"tx_{i}") for i in range(4)
        ]
        with patch.object(
            detector, "_fetch_recent_trades", new_callable=AsyncMock, return_value=trades
        ):
            await detector.detect_signals()

        assert detector.window_size == _EXPECTED_TRADE_COUNT

        # Second poll returns same trades — should add 0 new
        with patch.object(
            detector, "_fetch_recent_trades", new_callable=AsyncMock, return_value=[]
        ):
            await detector.detect_signals()

        assert detector.window_size == _EXPECTED_TRADE_COUNT

    @pytest.mark.asyncio
    async def test_filters_too_long_windows(self) -> None:
        """Skip markets whose window duration exceeds max_window_seconds."""
        detector = SignalDetector(
            whale_address=_ADDRESS,
            min_bias=Decimal("1.5"),
            min_trades=3,
            lookback_seconds=300,
            min_time_to_start=0,
            max_window_seconds=300,
        )
        trades = [
            _make_trade(
                outcome="Up",
                size=100.0,
                price=0.70,
                tx_hash=f"tx_{i}",
                title="Bitcoin Up or Down - March 13, 6PM ET",
            )
            for i in range(4)
        ]
        with patch.object(
            detector, "_fetch_recent_trades", new_callable=AsyncMock, return_value=trades
        ):
            signals = await detector.detect_signals()
        assert signals == []

    @pytest.mark.asyncio
    async def test_filters_too_soon_windows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Skip markets whose window starts sooner than min_time_to_start."""
        window = parse_time_window("Bitcoin Up or Down - March 13, 6PM ET", _FUTURE_TS)
        assert window is not None
        window_start = window[0]

        fake_now = window_start - _SECONDS_BEFORE_START
        monkeypatch.setattr("time.time", lambda: fake_now)

        detector = SignalDetector(
            whale_address=_ADDRESS,
            min_bias=Decimal("1.5"),
            min_trades=3,
            lookback_seconds=300,
            min_time_to_start=60,
        )
        trades = [
            _make_trade(
                outcome="Up",
                size=100.0,
                price=0.70,
                tx_hash=f"tx_{i}",
                timestamp=fake_now,
            )
            for i in range(4)
        ]
        with patch.object(
            detector, "_fetch_recent_trades", new_callable=AsyncMock, return_value=trades
        ):
            signals = await detector.detect_signals()
        assert signals == []
