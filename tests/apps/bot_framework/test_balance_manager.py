"""Tests for the shared BalanceManager service."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from trading_tools.apps.bot_framework.balance_manager import BalanceManager
from trading_tools.clients.polymarket.models import Balance


class TestBalanceManager:
    """Tests for USDC balance management via the CLOB API."""

    @pytest.fixture
    def mock_client(self) -> AsyncMock:
        """Create a mock PolymarketClient with balance endpoints."""
        client = AsyncMock()
        client.sync_balance = AsyncMock()
        client.get_balance = AsyncMock(
            return_value=Balance(
                asset_type="COLLATERAL", balance=Decimal("42.50"), allowance=Decimal(1000)
            )
        )
        client.get_portfolio_value = AsyncMock(return_value=Decimal("55.00"))
        return client

    @pytest.fixture
    def manager(self, mock_client: AsyncMock) -> BalanceManager:
        """Create a BalanceManager with mock client."""
        return BalanceManager(client=mock_client)

    @pytest.mark.asyncio
    async def test_refresh_fetches_balance(
        self, manager: BalanceManager, mock_client: AsyncMock
    ) -> None:
        """Refresh fetches and caches the USDC balance."""
        result = await manager.refresh()

        mock_client.sync_balance.assert_called_once_with("COLLATERAL")
        mock_client.get_balance.assert_called_once_with("COLLATERAL")
        assert result == Decimal("42.50")
        assert manager.balance == Decimal("42.50")

    @pytest.mark.asyncio
    async def test_refresh_with_portfolio(
        self, manager: BalanceManager, mock_client: AsyncMock
    ) -> None:
        """Refresh with include_portfolio also fetches portfolio value."""
        await manager.refresh(include_portfolio=True)

        mock_client.get_portfolio_value.assert_called_once()
        assert manager.portfolio_value == Decimal("55.00")

    @pytest.mark.asyncio
    async def test_refresh_without_portfolio_skips_value(
        self, manager: BalanceManager, mock_client: AsyncMock
    ) -> None:
        """Refresh without include_portfolio does not fetch portfolio value."""
        await manager.refresh()

        mock_client.get_portfolio_value.assert_not_called()
        assert manager.portfolio_value == Decimal(0)

    @pytest.mark.asyncio
    async def test_refresh_survives_api_failure(
        self, manager: BalanceManager, mock_client: AsyncMock
    ) -> None:
        """Return last known balance when the API call fails."""
        # First successful refresh
        await manager.refresh()
        assert manager.balance == Decimal("42.50")

        # Second refresh fails
        mock_client.get_balance.side_effect = RuntimeError("API down")
        result = await manager.refresh()

        assert result == Decimal("42.50")

    @pytest.mark.asyncio
    async def test_initial_balance_is_zero(self, manager: BalanceManager) -> None:
        """Balance starts at zero before first refresh."""
        assert manager.balance == Decimal(0)
        assert manager.portfolio_value == Decimal(0)
