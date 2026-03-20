"""Tests for the whale signal client."""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from trading_tools.apps.whale_copy.signal import WhaleSignalClient
from trading_tools.core.models import ZERO

_CID = "0xcondition123"


class TestWhaleSignalClient:
    """Test whale directional signal aggregation from the Data API."""

    @pytest.fixture
    def whale_addresses(self) -> tuple[str, ...]:
        """Return sample whale wallet addresses."""
        return ("0xwhale1", "0xwhale2")

    @pytest.fixture
    def condition_id(self) -> str:
        """Return a sample condition ID."""
        return _CID

    @pytest.mark.asyncio
    async def test_no_trades_returns_none(
        self, whale_addresses: tuple[str, ...], condition_id: str
    ) -> None:
        """Return (None, ZERO) when no whale trades exist."""
        transport = httpx.MockTransport(lambda _request: httpx.Response(200, json=[]))
        async_client = httpx.AsyncClient(
            transport=transport, base_url="https://data-api.polymarket.com"
        )
        client = WhaleSignalClient(whale_addresses=whale_addresses, _client=async_client)

        side, conviction = await client.get_direction(condition_id)

        assert side is None
        assert conviction == ZERO

    @pytest.mark.asyncio
    async def test_up_favoured(self, whale_addresses: tuple[str, ...], condition_id: str) -> None:
        """Return Up when whales have more dollar volume on Up side."""
        trades = [
            {"side": "BUY", "outcome": "Up", "size": "100", "price": "0.50", "conditionId": _CID},
            {"side": "BUY", "outcome": "Down", "size": "20", "price": "0.50", "conditionId": _CID},
        ]
        transport = httpx.MockTransport(lambda _request: httpx.Response(200, json=trades))
        async_client = httpx.AsyncClient(
            transport=transport, base_url="https://data-api.polymarket.com"
        )
        client = WhaleSignalClient(whale_addresses=whale_addresses, _client=async_client)

        side, conviction = await client.get_direction(condition_id)

        assert side == "Up"
        # Two whales each return same trades: up=2*(100*0.50)=100, down=2*(20*0.50)=20
        # conviction = 100/20 = 5.0
        assert conviction == Decimal(5)

    @pytest.mark.asyncio
    async def test_down_favoured(self, whale_addresses: tuple[str, ...], condition_id: str) -> None:
        """Return Down when whales have more dollar volume on Down side."""
        trades = [
            {"side": "BUY", "outcome": "Up", "size": "10", "price": "0.40", "conditionId": _CID},
            {"side": "BUY", "outcome": "Down", "size": "100", "price": "0.60", "conditionId": _CID},
        ]
        transport = httpx.MockTransport(lambda _request: httpx.Response(200, json=trades))
        async_client = httpx.AsyncClient(
            transport=transport, base_url="https://data-api.polymarket.com"
        )
        client = WhaleSignalClient(whale_addresses=whale_addresses, _client=async_client)

        side, conviction = await client.get_direction(condition_id)

        assert side == "Down"
        # Two whales: up=2*(10*0.4)=8, down=2*(100*0.6)=120
        # conviction = 120/8 = 15.0
        assert conviction == Decimal(15)

    @pytest.mark.asyncio
    async def test_sell_trades_ignored(
        self, whale_addresses: tuple[str, ...], condition_id: str
    ) -> None:
        """Ignore SELL trades when computing directional signal."""
        trades = [
            {"side": "SELL", "outcome": "Up", "size": "1000", "price": "0.50", "conditionId": _CID},
            {"side": "BUY", "outcome": "Down", "size": "10", "price": "0.50", "conditionId": _CID},
        ]
        transport = httpx.MockTransport(lambda _request: httpx.Response(200, json=trades))
        async_client = httpx.AsyncClient(
            transport=transport, base_url="https://data-api.polymarket.com"
        )
        client = WhaleSignalClient(whale_addresses=whale_addresses, _client=async_client)

        side, _conviction = await client.get_direction(condition_id)

        assert side == "Down"

    @pytest.mark.asyncio
    async def test_api_error_gracefully_handled(
        self, whale_addresses: tuple[str, ...], condition_id: str
    ) -> None:
        """Return (None, ZERO) when API returns an error."""
        transport = httpx.MockTransport(
            lambda _request: httpx.Response(500, json={"error": "internal"})
        )
        async_client = httpx.AsyncClient(
            transport=transport, base_url="https://data-api.polymarket.com"
        )
        client = WhaleSignalClient(whale_addresses=whale_addresses, _client=async_client)

        side, conviction = await client.get_direction(condition_id)

        assert side is None
        assert conviction == ZERO

    @pytest.mark.asyncio
    async def test_close(self, whale_addresses: tuple[str, ...]) -> None:
        """Verify close disposes the httpx client."""
        transport = httpx.MockTransport(lambda _request: httpx.Response(200, json=[]))
        async_client = httpx.AsyncClient(
            transport=transport, base_url="https://data-api.polymarket.com"
        )
        client = WhaleSignalClient(whale_addresses=whale_addresses, _client=async_client)

        await client.close()
        assert async_client.is_closed

    @pytest.mark.asyncio
    async def test_one_sided_conviction_uses_absolute_volume(
        self, whale_addresses: tuple[str, ...], condition_id: str
    ) -> None:
        """When only one side has volume, conviction equals that side's dollar volume."""
        trades = [
            {"side": "BUY", "outcome": "Up", "size": "50", "price": "0.40", "conditionId": _CID},
        ]
        transport = httpx.MockTransport(lambda _request: httpx.Response(200, json=trades))
        async_client = httpx.AsyncClient(
            transport=transport, base_url="https://data-api.polymarket.com"
        )
        client = WhaleSignalClient(whale_addresses=whale_addresses, _client=async_client)

        side, conviction = await client.get_direction(condition_id)

        assert side == "Up"
        # Two whales each return 50*0.4=20, total=40, down=0 -> conviction = total volume
        assert conviction == Decimal(40)

    @pytest.mark.asyncio
    async def test_window_start_filters_old_trades(
        self, whale_addresses: tuple[str, ...], condition_id: str
    ) -> None:
        """Ignore trades with timestamps before window_start_ts."""
        _window_start = 1000
        trades = [
            # Old trade before window — should be ignored
            {
                "side": "BUY",
                "outcome": "Up",
                "size": "500",
                "price": "0.50",
                "conditionId": _CID,
                "timestamp": 900,
            },
            # Current window trade
            {
                "side": "BUY",
                "outcome": "Down",
                "size": "10",
                "price": "0.50",
                "conditionId": _CID,
                "timestamp": 1050,
            },
        ]
        transport = httpx.MockTransport(lambda _request: httpx.Response(200, json=trades))
        async_client = httpx.AsyncClient(
            transport=transport, base_url="https://data-api.polymarket.com"
        )
        client = WhaleSignalClient(whale_addresses=whale_addresses, _client=async_client)

        side, _conviction = await client.get_direction(condition_id, window_start_ts=_window_start)

        # Only the Down trade at ts=1050 should count; Up trade at ts=900 filtered out
        assert side == "Down"

    @pytest.mark.asyncio
    async def test_no_trades_in_window(
        self, whale_addresses: tuple[str, ...], condition_id: str
    ) -> None:
        """Return (None, ZERO) when all trades are before window start."""
        trades = [
            {
                "side": "BUY",
                "outcome": "Up",
                "size": "100",
                "price": "0.50",
                "conditionId": _CID,
                "timestamp": 500,
            },
        ]
        transport = httpx.MockTransport(lambda _request: httpx.Response(200, json=trades))
        async_client = httpx.AsyncClient(
            transport=transport, base_url="https://data-api.polymarket.com"
        )
        client = WhaleSignalClient(whale_addresses=whale_addresses, _client=async_client)

        side, conviction = await client.get_direction(condition_id, window_start_ts=1000)

        assert side is None
        assert conviction == ZERO

    @pytest.mark.asyncio
    async def test_wrong_condition_id_filtered_out(
        self, whale_addresses: tuple[str, ...], condition_id: str
    ) -> None:
        """Ignore trades with a different condition ID (client-side filter)."""
        trades = [
            {
                "side": "BUY",
                "outcome": "Up",
                "size": "100",
                "price": "0.50",
                "conditionId": "0xdifferent_market",
            },
            {"side": "BUY", "outcome": "Down", "size": "10", "price": "0.50", "conditionId": _CID},
        ]
        transport = httpx.MockTransport(lambda _request: httpx.Response(200, json=trades))
        async_client = httpx.AsyncClient(
            transport=transport, base_url="https://data-api.polymarket.com"
        )
        client = WhaleSignalClient(whale_addresses=whale_addresses, _client=async_client)

        side, _conviction = await client.get_direction(condition_id)

        # Only the Down trade with matching CID should count
        assert side == "Down"
