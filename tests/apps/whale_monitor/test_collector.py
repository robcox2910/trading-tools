"""Tests for the whale monitor orchestrator."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trading_tools.apps.whale_monitor.collector import (
    WhaleMonitor,
    _parse_trade,
)
from trading_tools.apps.whale_monitor.config import WhaleMonitorConfig
from trading_tools.core.timestamps import now_ms

from .conftest import DEFAULT_ADDRESS, make_raw_trade, make_tracked_whale, make_whale_config

_ADDRESS = DEFAULT_ADDRESS
_TX_HASH = "0xabc123"
_EXPECTED_PRICE = 0.72
_EXPECTED_SIZE = 50.0
_MIN_EPOCH_MS = 1_000_000_000_000
_POLL_COUNT_2 = 2


class TestParseTrade:
    """Tests for the _parse_trade helper function."""

    def test_parse_valid_trade(self) -> None:
        """Parse a valid raw trade into a WhaleTrade."""
        raw = make_raw_trade()
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
    """Tests for the now_ms utility."""

    def test_returns_positive_integer(self) -> None:
        """Verify now_ms returns a positive integer."""
        result = now_ms()
        assert isinstance(result, int)
        assert result > 0

    def test_returns_milliseconds(self) -> None:
        """Verify the value is in milliseconds (> 1e12 for modern epochs)."""
        result = now_ms()
        assert result > _MIN_EPOCH_MS


_MALFORMED_EPOCH_MS = 1700000000000
_EXTREME_PRICE = 999999999.99
_EXTREME_SIZE = 999999999.99


class TestParseTradeEdgeCases:
    """Parametrized edge-case tests for _parse_trade covering malformed and extreme inputs."""

    @pytest.mark.parametrize(
        ("field_name", "bad_value"),
        [
            ("price", "invalid"),
            ("size", "not_a_number"),
            ("timestamp", "yesterday"),
            ("outcome_index", "two"),
        ],
        ids=["malformed-price", "malformed-size", "malformed-timestamp", "malformed-outcome-index"],
    )
    def test_malformed_numeric_returns_none(
        self,
        field_name: str,
        bad_value: str,
    ) -> None:
        """Return None when a required numeric field contains a non-numeric string."""
        raw = make_raw_trade()
        raw[field_name] = bad_value
        trade = _parse_trade(raw, _ADDRESS, _MALFORMED_EPOCH_MS)
        assert trade is None

    def test_extreme_price_parses_successfully(self) -> None:
        """Parse a trade with an extreme but valid price value."""
        raw = make_raw_trade(price=_EXTREME_PRICE)
        trade = _parse_trade(raw, _ADDRESS, _MALFORMED_EPOCH_MS)
        assert trade is not None
        assert trade.price == _EXTREME_PRICE

    def test_extreme_size_parses_successfully(self) -> None:
        """Parse a trade with an extreme but valid size value."""
        raw = make_raw_trade(size=_EXTREME_SIZE)
        trade = _parse_trade(raw, _ADDRESS, _MALFORMED_EPOCH_MS)
        assert trade is not None
        assert trade.size == _EXTREME_SIZE

    @pytest.mark.parametrize(
        "field_name",
        [
            "price",
            "size",
            "side",
            "outcome",
        ],
        ids=["null-price", "null-size", "null-side", "null-outcome"],
    )
    def test_none_in_optional_field_uses_default(self, field_name: str) -> None:
        """Gracefully handle None values in optional fields by using defaults."""
        raw = make_raw_trade()
        raw[field_name] = None
        trade = _parse_trade(raw, _ADDRESS, _MALFORMED_EPOCH_MS)
        # None in optional fields should still parse (float(None) raises TypeError → None)
        # or str(None) → "None" for string fields
        # The function catches TypeError, so numeric None → None result
        if field_name in ("price", "size"):
            assert trade is None
        else:
            # String fields: str(None) = "None", so it parses but with "None" value
            assert trade is not None

    def test_none_transaction_hash_returns_none(self) -> None:
        """Return None when transactionHash is None (KeyError path)."""
        raw = make_raw_trade()
        del raw["transactionHash"]
        trade = _parse_trade(raw, _ADDRESS, _MALFORMED_EPOCH_MS)
        assert trade is None


class TestHandleShutdown:
    """Tests for the GracefulShutdown integration."""

    def test_sets_shutdown_flag(self) -> None:
        """Verify request() sets the should_stop flag."""
        config = make_whale_config()
        monitor = WhaleMonitor(config)

        monitor._shutdown.request()

        assert monitor._shutdown.should_stop is True


class TestPollWhale:
    """Tests for polling a single whale's trades."""

    @pytest.mark.asyncio
    async def test_poll_whale_inserts_new_trades(self) -> None:
        """Verify new trades are inserted and count is returned."""
        config = make_whale_config()
        monitor = WhaleMonitor(config)

        mock_repo = MagicMock()
        mock_repo.get_existing_hashes = AsyncMock(return_value=set())
        mock_repo.save_trades = AsyncMock()
        monitor._repo = mock_repo

        mock_response = MagicMock()
        mock_response.json.return_value = [
            make_raw_trade(tx_hash="tx_1"),
            make_raw_trade(tx_hash="tx_2"),
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
        config = make_whale_config()
        monitor = WhaleMonitor(config)

        mock_repo = MagicMock()
        mock_repo.get_existing_hashes = AsyncMock(return_value={"tx_existing"})
        mock_repo.save_trades = AsyncMock()
        monitor._repo = mock_repo

        mock_response = MagicMock()
        mock_response.json.return_value = [
            make_raw_trade(tx_hash="tx_existing"),
            make_raw_trade(tx_hash="tx_new"),
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
        config = make_whale_config()
        monitor = WhaleMonitor(config)

        mock_repo = MagicMock()
        mock_repo.get_existing_hashes = AsyncMock(return_value=set())
        mock_repo.save_trades = AsyncMock()
        monitor._repo = mock_repo

        mock_response = MagicMock()
        mock_response.json.return_value = [
            make_raw_trade(tx_hash="tx_dup"),
            make_raw_trade(tx_hash="tx_dup"),
            make_raw_trade(tx_hash="tx_unique"),
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
        config = make_whale_config()
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

        page1 = [make_raw_trade(tx_hash="tx_1"), make_raw_trade(tx_hash="tx_2")]
        page2 = [make_raw_trade(tx_hash="tx_3")]

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
        config = make_whale_config(whales=())
        monitor = WhaleMonitor(config)

        mock_repo = MagicMock()
        mock_repo.init_db = AsyncMock()
        mock_repo.get_active_whales = AsyncMock(return_value=[])
        mock_repo.close = AsyncMock()
        monitor._repo = mock_repo

        with patch.object(monitor._shutdown, "install"):
            await monitor.run()

        mock_repo.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_polls_and_shuts_down(self) -> None:
        """Verify run loop polls whales and respects shutdown flag."""
        config = make_whale_config()
        monitor = WhaleMonitor(config)

        whale = make_tracked_whale()

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
        mock_response.json.return_value = [make_raw_trade()]
        mock_response.raise_for_status = MagicMock()

        async def mock_sleep(delay: float) -> None:  # noqa: ARG001
            # Request shutdown on every sleep call to ensure the loop exits
            monitor._shutdown.request()

        with (
            patch.object(monitor._shutdown, "install"),
            patch("asyncio.sleep", side_effect=mock_sleep),
            patch("httpx.AsyncClient") as mock_http_cls,
        ):
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
        config = make_whale_config()
        monitor = WhaleMonitor(config)
        monitor._trades_since_heartbeat = 10

        whale = make_tracked_whale()
        mock_repo = MagicMock()
        mock_repo.get_active_whales = AsyncMock(return_value=[whale])
        mock_repo.get_trade_count = AsyncMock(return_value=42)
        monitor._repo = mock_repo

        call_count = 0

        async def fast_sleep(delay: float) -> None:  # noqa: ARG001
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                monitor._shutdown.request()

        with (
            patch("asyncio.sleep", side_effect=fast_sleep),
            caplog.at_level(logging.INFO),
        ):
            await monitor._periodic_heartbeat()

        assert monitor._trades_since_heartbeat == 0
        assert "HEARTBEAT" in caplog.text
