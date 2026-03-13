"""Tests for the whale monitor orchestrator."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trading_tools.apps.whale_monitor.collector import (
    WhaleMonitor,
    _now_ms,
    _parse_trade,
)
from trading_tools.apps.whale_monitor.config import WhaleMonitorConfig
from trading_tools.apps.whale_monitor.models import TrackedWhale

_ADDRESS = "0xa45fe11dd1420fca906ceac2c067844379a42429"
_TX_HASH = "0xabc123"
_EXPECTED_PRICE = 0.72
_EXPECTED_SIZE = 50.0
_MIN_EPOCH_MS = 1_000_000_000_000
_POLL_COUNT_2 = 2


def _make_config(
    *,
    db_url: str = "sqlite+aiosqlite:///:memory:",
    whales: tuple[str, ...] = (_ADDRESS,),
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


def _make_raw_trade(
    tx_hash: str = _TX_HASH,
    price: float = 0.72,
    size: float = 50.0,
) -> dict[str, Any]:
    """Create a sample raw trade dict from the Data API.

    Args:
        tx_hash: Transaction hash.
        price: Trade price.
        size: Trade size.

    Returns:
        Trade dictionary matching Data API format.

    """
    return {
        "transactionHash": tx_hash,
        "side": "BUY",
        "asset_id": "asset_test",
        "condition_id": "cond_test",
        "size": size,
        "price": price,
        "timestamp": 1700000000,
        "market": "BTC Up/Down",
        "slug": "btc-updown",
        "outcome": "Up",
        "outcome_index": 0,
    }


def _make_tracked_whale(
    address: str = _ADDRESS,
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
        added_at=1700000000,
        active=True,
    )


class TestParseTrade:
    """Tests for the _parse_trade helper function."""

    def test_parse_valid_trade(self) -> None:
        """Parse a valid raw trade into a WhaleTrade."""
        raw = _make_raw_trade()
        trade = _parse_trade(raw, _ADDRESS, 1700000000000)

        assert trade is not None
        assert trade.whale_address == _ADDRESS.lower()
        assert trade.transaction_hash == _TX_HASH
        assert trade.price == _EXPECTED_PRICE
        assert trade.size == _EXPECTED_SIZE
        assert trade.side == "BUY"
        assert trade.outcome == "Up"

    def test_parse_malformed_trade_missing_hash(self) -> None:
        """Return None when transactionHash is missing."""
        raw = {"side": "BUY", "price": 0.72}
        trade = _parse_trade(raw, _ADDRESS, 1700000000000)
        assert trade is None

    def test_parse_trade_alternative_keys(self) -> None:
        """Parse a trade using alternative API key names."""
        raw = {
            "transactionHash": _TX_HASH,
            "side": "SELL",
            "assetId": "asset_alt",
            "conditionId": "cond_alt",
            "size": 25.0,
            "price": 0.55,
            "timestamp": 1700000000,
            "title": "ETH Up/Down",
            "market_slug": "eth-updown",
            "outcome": "Down",
            "outcomeIndex": 1,
        }
        trade = _parse_trade(raw, _ADDRESS, 1700000000000)

        assert trade is not None
        assert trade.asset_id == "asset_alt"
        assert trade.condition_id == "cond_alt"
        assert trade.outcome_index == 1


class TestNowMs:
    """Tests for the _now_ms utility."""

    def test_returns_positive_integer(self) -> None:
        """Verify _now_ms returns a positive integer."""
        result = _now_ms()
        assert isinstance(result, int)
        assert result > 0

    def test_returns_milliseconds(self) -> None:
        """Verify the value is in milliseconds (> 1e12 for modern epochs)."""
        result = _now_ms()
        assert result > _MIN_EPOCH_MS


class TestHandleShutdown:
    """Tests for the shutdown signal handler."""

    def test_sets_shutdown_flag(self) -> None:
        """Verify _handle_shutdown sets the shutdown flag."""
        config = _make_config()
        monitor = WhaleMonitor(config)

        monitor._handle_shutdown()

        assert monitor._shutdown is True


class TestPollWhale:
    """Tests for polling a single whale's trades."""

    @pytest.mark.asyncio
    async def test_poll_whale_inserts_new_trades(self) -> None:
        """Verify new trades are inserted and count is returned."""
        config = _make_config()
        monitor = WhaleMonitor(config)

        mock_repo = MagicMock()
        mock_repo.get_existing_hashes = AsyncMock(return_value=set())
        mock_repo.save_trades = AsyncMock()
        monitor._repo = mock_repo

        mock_response = MagicMock()
        mock_response.json.return_value = [
            _make_raw_trade(tx_hash="tx_1"),
            _make_raw_trade(tx_hash="tx_2"),
        ]
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        count = await monitor._poll_whale(mock_client, _ADDRESS)

        assert count == _POLL_COUNT_2
        mock_repo.save_trades.assert_awaited_once()
        saved = mock_repo.save_trades.call_args[0][0]
        assert len(saved) == _POLL_COUNT_2

    @pytest.mark.asyncio
    async def test_poll_whale_deduplicates(self) -> None:
        """Verify existing trades are skipped."""
        config = _make_config()
        monitor = WhaleMonitor(config)

        mock_repo = MagicMock()
        mock_repo.get_existing_hashes = AsyncMock(return_value={"tx_existing"})
        mock_repo.save_trades = AsyncMock()
        monitor._repo = mock_repo

        mock_response = MagicMock()
        mock_response.json.return_value = [
            _make_raw_trade(tx_hash="tx_existing"),
            _make_raw_trade(tx_hash="tx_new"),
        ]
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        count = await monitor._poll_whale(mock_client, _ADDRESS)

        assert count == 1
        saved = mock_repo.save_trades.call_args[0][0]
        assert len(saved) == 1
        assert saved[0].transaction_hash == "tx_new"

    @pytest.mark.asyncio
    async def test_poll_whale_deduplicates_within_batch(self) -> None:
        """Verify duplicate hashes within the same API response are skipped."""
        config = _make_config()
        monitor = WhaleMonitor(config)

        mock_repo = MagicMock()
        mock_repo.get_existing_hashes = AsyncMock(return_value=set())
        mock_repo.save_trades = AsyncMock()
        monitor._repo = mock_repo

        mock_response = MagicMock()
        mock_response.json.return_value = [
            _make_raw_trade(tx_hash="tx_dup"),
            _make_raw_trade(tx_hash="tx_dup"),
            _make_raw_trade(tx_hash="tx_unique"),
        ]
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        count = await monitor._poll_whale(mock_client, _ADDRESS)

        assert count == _POLL_COUNT_2
        saved = mock_repo.save_trades.call_args[0][0]
        assert len(saved) == _POLL_COUNT_2
        hashes = {t.transaction_hash for t in saved}
        assert hashes == {"tx_dup", "tx_unique"}

    @pytest.mark.asyncio
    async def test_poll_whale_empty_response(self) -> None:
        """Return zero when API returns no trades."""
        config = _make_config()
        monitor = WhaleMonitor(config)

        mock_repo = MagicMock()
        monitor._repo = mock_repo

        mock_response = MagicMock()
        mock_response.json.return_value = []
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        count = await monitor._poll_whale(mock_client, _ADDRESS)

        assert count == 0

    @pytest.mark.asyncio
    async def test_poll_whale_paginates(self) -> None:
        """Verify pagination fetches multiple pages."""
        config = WhaleMonitorConfig(
            db_url="sqlite+aiosqlite:///:memory:",
            whales=(_ADDRESS,),
            api_limit=2,
            max_offset=2,
        )
        monitor = WhaleMonitor(config)

        mock_repo = MagicMock()
        mock_repo.get_existing_hashes = AsyncMock(return_value=set())
        mock_repo.save_trades = AsyncMock()
        monitor._repo = mock_repo

        page1 = [_make_raw_trade(tx_hash="tx_1"), _make_raw_trade(tx_hash="tx_2")]
        page2 = [_make_raw_trade(tx_hash="tx_3")]

        mock_resp1 = MagicMock()
        mock_resp1.json.return_value = page1
        mock_resp1.raise_for_status = MagicMock()

        mock_resp2 = MagicMock()
        mock_resp2.json.return_value = page2
        mock_resp2.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[mock_resp1, mock_resp2])

        count = await monitor._poll_whale(mock_client, _ADDRESS)

        assert count == 3  # noqa: PLR2004
        assert mock_client.get.await_count == _POLL_COUNT_2


class TestWhaleMonitorEndToEnd:
    """End-to-end tests for the monitor run loop."""

    @pytest.mark.asyncio
    async def test_run_no_whales_exits(self) -> None:
        """Return immediately when no whales are configured."""
        config = _make_config(whales=())
        monitor = WhaleMonitor(config)

        mock_repo = MagicMock()
        mock_repo.init_db = AsyncMock()
        mock_repo.get_active_whales = AsyncMock(return_value=[])
        mock_repo.close = AsyncMock()
        monitor._repo = mock_repo

        with patch("asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value = MagicMock()
            await monitor.run()

        mock_repo.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_polls_and_shuts_down(self) -> None:
        """Verify run loop polls whales and respects shutdown flag."""
        config = _make_config()
        monitor = WhaleMonitor(config)

        whale = _make_tracked_whale()

        mock_repo = MagicMock()
        mock_repo.init_db = AsyncMock()
        mock_repo.add_whale = AsyncMock(return_value=whale)
        mock_repo.get_active_whales = AsyncMock(return_value=[whale])
        mock_repo.get_existing_hashes = AsyncMock(return_value=set())
        mock_repo.save_trades = AsyncMock()
        mock_repo.get_trade_count = AsyncMock(return_value=0)
        mock_repo.close = AsyncMock()
        monitor._repo = mock_repo

        mock_response = MagicMock()
        mock_response.json.return_value = [_make_raw_trade()]
        mock_response.raise_for_status = MagicMock()

        call_count = 0

        async def mock_sleep(delay: float) -> None:  # noqa: ARG001
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                monitor._shutdown = True

        with (
            patch("asyncio.get_running_loop") as mock_loop,
            patch("asyncio.sleep", side_effect=mock_sleep),
            patch("httpx.AsyncClient") as mock_http_cls,
        ):
            mock_loop.return_value = MagicMock()
            mock_http = AsyncMock()
            mock_http.get = AsyncMock(return_value=mock_response)
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=None)
            mock_http_cls.return_value = mock_http

            await monitor.run()

        mock_repo.save_trades.assert_awaited()

    @pytest.mark.asyncio
    async def test_periodic_heartbeat_logs(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Verify heartbeat logs stats and resets counter."""
        config = _make_config()
        monitor = WhaleMonitor(config)
        monitor._trades_since_heartbeat = 10

        whale = _make_tracked_whale()
        mock_repo = MagicMock()
        mock_repo.get_active_whales = AsyncMock(return_value=[whale])
        mock_repo.get_trade_count = AsyncMock(return_value=42)
        monitor._repo = mock_repo

        call_count = 0

        async def fast_sleep(delay: float) -> None:  # noqa: ARG001
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                monitor._shutdown = True

        with (
            patch("asyncio.sleep", side_effect=fast_sleep),
            caplog.at_level(logging.INFO),
        ):
            await monitor._periodic_heartbeat()

        assert monitor._trades_since_heartbeat == 0
        assert "WHALE-MONITOR" in caplog.text
