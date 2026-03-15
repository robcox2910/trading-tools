"""Tests for the PositionRedeemer shared service."""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from trading_tools.apps.bot_framework.redeemer import PositionRedeemer
from trading_tools.clients.polymarket.models import RedeemablePosition

_MIN_ORDER_SIZE = Decimal(5)


def _make_position(
    condition_id: str = "0xresolved1",
    size: Decimal = Decimal("10.0"),
    title: str = "ETH Up or Down - Feb 24",
) -> RedeemablePosition:
    """Create a RedeemablePosition for testing.

    Args:
        condition_id: Market condition ID.
        size: Number of tokens held.
        title: Market title.

    Returns:
        A RedeemablePosition instance.

    """
    return RedeemablePosition(
        condition_id=condition_id,
        token_id=f"tok_{condition_id}",
        outcome="Up",
        size=size,
        title=title,
    )


class TestRedeemerNoRedeemable:
    """Tests for when no redeemable positions exist."""

    @pytest.mark.asyncio
    async def test_no_redeemable_positions_no_task(self) -> None:
        """Create no background task when there are no redeemable positions."""
        client = AsyncMock()
        client.get_redeemable_positions = AsyncMock(return_value=[])
        redeemer = PositionRedeemer(client=client)

        await redeemer.redeem_if_available()

        assert redeemer.task is None
        client.redeem_positions.assert_not_called()


class TestRedeemerFiltering:
    """Tests for position size filtering."""

    @pytest.mark.asyncio
    async def test_filters_positions_below_min_size(self, caplog: pytest.LogCaptureFixture) -> None:
        """Skip positions below the minimum order size."""
        small = _make_position(size=Decimal("2.0"), title="Small position")
        client = AsyncMock()
        client.get_redeemable_positions = AsyncMock(return_value=[small])
        redeemer = PositionRedeemer(client=client)

        with caplog.at_level(
            logging.INFO,
            logger="trading_tools.apps.bot_framework.redeemer",
        ):
            await redeemer.redeem_if_available()

        assert redeemer.task is None
        client.redeem_positions.assert_not_called()
        assert any("REDEEM skip" in msg for msg in caplog.messages)

    @pytest.mark.asyncio
    async def test_custom_min_order_size(self) -> None:
        """Respect a custom min_order_size threshold."""
        pos = _make_position(size=Decimal("8.0"))
        client = AsyncMock()
        client.get_redeemable_positions = AsyncMock(return_value=[pos])
        client.redeem_positions = AsyncMock(return_value=1)
        high_min = Decimal(10)
        redeemer = PositionRedeemer(client=client, min_order_size=high_min)

        await redeemer.redeem_if_available()

        assert redeemer.task is None
        client.redeem_positions.assert_not_called()


class TestRedeemerExecution:
    """Tests for successful redemption execution."""

    @pytest.mark.asyncio
    async def test_spawns_background_task(self) -> None:
        """Spawn a background task for qualifying positions."""
        pos = _make_position()
        client = AsyncMock()
        client.get_redeemable_positions = AsyncMock(return_value=[pos])
        client.redeem_positions = AsyncMock(return_value=1)
        redeemer = PositionRedeemer(client=client)

        await redeemer.redeem_if_available()

        assert redeemer.task is not None
        await redeemer.task
        client.redeem_positions.assert_called_once_with(["0xresolved1"])

    @pytest.mark.asyncio
    async def test_redeems_multiple_positions(self) -> None:
        """Redeem multiple qualifying positions in a single call."""
        positions = [
            _make_position(condition_id="0xa"),
            _make_position(condition_id="0xb"),
        ]
        client = AsyncMock()
        client.get_redeemable_positions = AsyncMock(return_value=positions)
        client.redeem_positions = AsyncMock(return_value=2)
        redeemer = PositionRedeemer(client=client)

        await redeemer.redeem_if_available()

        assert redeemer.task is not None
        await redeemer.task
        expected_ids = 2
        assert len(client.redeem_positions.call_args[0][0]) == expected_ids


class TestRedeemerErrorHandling:
    """Tests for error handling in redemption."""

    @pytest.mark.asyncio
    async def test_discovery_error_logged_not_raised(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Log discovery errors without propagation."""
        client = AsyncMock()
        client.get_redeemable_positions = AsyncMock(side_effect=RuntimeError("API down"))
        redeemer = PositionRedeemer(client=client)

        with caplog.at_level(
            logging.WARNING,
            logger="trading_tools.apps.bot_framework.redeemer",
        ):
            await redeemer.redeem_if_available()

        assert redeemer.task is None
        assert any("Failed to discover" in msg for msg in caplog.messages)

    @pytest.mark.asyncio
    async def test_ctf_failure_logged_not_raised(self, caplog: pytest.LogCaptureFixture) -> None:
        """Log CTF redemption errors without propagation."""
        pos = _make_position()
        client = AsyncMock()
        client.get_redeemable_positions = AsyncMock(return_value=[pos])
        client.redeem_positions = AsyncMock(side_effect=Exception("RPC timeout"))
        redeemer = PositionRedeemer(client=client)

        with caplog.at_level(
            logging.WARNING,
            logger="trading_tools.apps.bot_framework.redeemer",
        ):
            await redeemer.redeem_if_available()
            assert redeemer.task is not None
            await redeemer.task

        assert any("CTF redemption failed" in msg for msg in caplog.messages)


class TestRedeemerTaskManagement:
    """Tests for background task lifecycle management."""

    @pytest.mark.asyncio
    async def test_cancels_previous_running_task(self, caplog: pytest.LogCaptureFixture) -> None:
        """Cancel a still-running previous task before starting a new one."""
        pos = _make_position()
        client = AsyncMock()
        client.get_redeemable_positions = AsyncMock(return_value=[pos])
        client.redeem_positions = AsyncMock(return_value=1)
        redeemer = PositionRedeemer(client=client)

        # Simulate a still-running task
        old_task = asyncio.create_task(asyncio.sleep(10))
        redeemer._task = old_task

        with caplog.at_level(
            logging.INFO,
            logger="trading_tools.apps.bot_framework.redeemer",
        ):
            await redeemer.redeem_if_available()

        assert old_task.cancelling()
        assert any("cancelling previous" in msg for msg in caplog.messages)
        assert redeemer.task is not None
        assert redeemer.task is not old_task

    @pytest.mark.asyncio
    async def test_skips_cancel_when_previous_task_done(self) -> None:
        """Do not cancel a completed previous task."""
        pos = _make_position()
        client = AsyncMock()
        client.get_redeemable_positions = AsyncMock(return_value=[pos])
        client.redeem_positions = AsyncMock(return_value=1)
        redeemer = PositionRedeemer(client=client)

        # Create a completed task
        done_task: asyncio.Task[None] = asyncio.create_task(asyncio.sleep(0))
        await done_task
        redeemer._task = done_task

        await redeemer.redeem_if_available()

        assert redeemer.task is not None
        assert redeemer.task is not done_task
