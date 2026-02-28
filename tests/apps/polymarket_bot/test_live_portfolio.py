"""Tests for LivePortfolio real order execution wrapper."""

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from trading_tools.apps.polymarket_bot.live_portfolio import LivePortfolio
from trading_tools.apps.polymarket_bot.models import LiveTrade
from trading_tools.clients.polymarket.exceptions import PolymarketAPIError
from trading_tools.clients.polymarket.models import Balance, OrderResponse
from trading_tools.core.models import ZERO, Side

_CONDITION_A = "cond_live_a"
_CONDITION_B = "cond_live_b"
_TOKEN_YES = "tok_yes_123"
_TOKEN_NO = "tok_no_456"
_ORDER_ID = "order_abc"
_TIMESTAMP = 1700000000
_INITIAL_BALANCE = Decimal("1000.00")
_WALLET_BALANCE = Decimal("1050.00")
_MAX_POSITION_PCT = Decimal("0.1")


def _mock_client(
    *,
    balance: Decimal = _INITIAL_BALANCE,
    wallet_balance: Decimal = _WALLET_BALANCE,
    order_id: str = _ORDER_ID,
    filled: Decimal = ZERO,
) -> AsyncMock:
    """Build a mock PolymarketClient for portfolio tests.

    Args:
        balance: USDC balance to return.
        wallet_balance: On-chain wallet balance to return.
        order_id: Order ID to return from place_order.
        filled: Filled amount to return from place_order.

    Returns:
        AsyncMock configured as a PolymarketClient.

    """
    client = AsyncMock()
    client.get_balance = AsyncMock(
        return_value=Balance(
            asset_type="COLLATERAL",
            balance=balance,
            allowance=Decimal(10000),
        ),
    )
    client.get_wallet_balance = AsyncMock(return_value=wallet_balance)
    client.place_order = AsyncMock(
        return_value=OrderResponse(
            order_id=order_id,
            status="matched",
            token_id=_TOKEN_YES,
            side="BUY",
            price=Decimal("0.60"),
            size=Decimal(10),
            filled=filled,
        ),
    )
    return client


class TestRefreshBalance:
    """Tests for balance refresh."""

    @pytest.mark.asyncio
    async def test_refresh_balance_updates_internal_state(self) -> None:
        """Verify refresh_balance fetches and stores the USDC balance."""
        client = _mock_client()
        portfolio = LivePortfolio(client, _MAX_POSITION_PCT)

        result = await portfolio.refresh_balance()

        assert result == _INITIAL_BALANCE
        assert portfolio.balance == _INITIAL_BALANCE
        client.get_balance.assert_awaited_once_with("COLLATERAL")

    @pytest.mark.asyncio
    async def test_refresh_balance_survives_api_error(self) -> None:
        """Return last known balance when the API call fails."""
        client = _mock_client()
        portfolio = LivePortfolio(client, _MAX_POSITION_PCT)

        # First call succeeds
        await portfolio.refresh_balance()
        assert portfolio.balance == _INITIAL_BALANCE

        # Second call fails — should keep the old balance
        client.get_balance = AsyncMock(
            side_effect=PolymarketAPIError(msg="Request exception!", status_code=500),
        )
        result = await portfolio.refresh_balance()
        assert result == _INITIAL_BALANCE
        assert portfolio.balance == _INITIAL_BALANCE


class TestOpenPosition:
    """Tests for opening live positions."""

    @pytest.mark.asyncio
    async def test_open_position_places_order(self) -> None:
        """Verify open_position calls place_order and records a LiveTrade."""
        client = _mock_client(filled=Decimal(10))
        portfolio = LivePortfolio(client, _MAX_POSITION_PCT)
        await portfolio.refresh_balance()

        trade = await portfolio.open_position(
            condition_id=_CONDITION_A,
            token_id=_TOKEN_YES,
            outcome="Yes",
            side=Side.BUY,
            price=Decimal("0.50"),
            quantity=Decimal(10),
            timestamp=_TIMESTAMP,
            reason="test buy",
            edge=Decimal("0.05"),
        )

        assert trade is not None
        assert isinstance(trade, LiveTrade)
        assert trade.order_id == _ORDER_ID
        assert trade.filled == Decimal(10)
        assert trade.side == Side.BUY
        client.place_order.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_open_position_records_trade(self) -> None:
        """Verify open_position adds the trade to the trades list."""
        client = _mock_client()
        portfolio = LivePortfolio(client, _MAX_POSITION_PCT)
        await portfolio.refresh_balance()

        await portfolio.open_position(
            condition_id=_CONDITION_A,
            token_id=_TOKEN_YES,
            outcome="Yes",
            side=Side.BUY,
            price=Decimal("0.50"),
            quantity=Decimal(10),
            timestamp=_TIMESTAMP,
            reason="test",
            edge=Decimal("0.05"),
        )

        assert len(portfolio.trades) == 1
        assert portfolio.trades[0].side == Side.BUY

    @pytest.mark.asyncio
    async def test_open_duplicate_rejected(self) -> None:
        """Verify opening a second position for the same market returns None."""
        client = _mock_client()
        portfolio = LivePortfolio(client, _MAX_POSITION_PCT)
        await portfolio.refresh_balance()

        await portfolio.open_position(
            condition_id=_CONDITION_A,
            token_id=_TOKEN_YES,
            outcome="Yes",
            side=Side.BUY,
            price=Decimal("0.50"),
            quantity=Decimal(10),
            timestamp=_TIMESTAMP,
            reason="first",
            edge=Decimal("0.05"),
        )
        result = await portfolio.open_position(
            condition_id=_CONDITION_A,
            token_id=_TOKEN_YES,
            outcome="Yes",
            side=Side.BUY,
            price=Decimal("0.50"),
            quantity=Decimal(10),
            timestamp=_TIMESTAMP + 1,
            reason="duplicate",
            edge=Decimal("0.05"),
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_open_exceeds_allocation_rejected(self) -> None:
        """Verify position exceeding max allocation is rejected."""
        client = _mock_client()
        portfolio = LivePortfolio(client, _MAX_POSITION_PCT)
        await portfolio.refresh_balance()

        # max allocation = 1000 * 0.1 = 100; cost = 0.50 * 300 = 150
        result = await portfolio.open_position(
            condition_id=_CONDITION_A,
            token_id=_TOKEN_YES,
            outcome="Yes",
            side=Side.BUY,
            price=Decimal("0.50"),
            quantity=Decimal(300),
            timestamp=_TIMESTAMP,
            reason="too big",
            edge=Decimal("0.05"),
        )

        assert result is None
        client.place_order.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_open_api_error_returns_none(self) -> None:
        """Verify API error during order placement returns None."""
        client = _mock_client()
        client.place_order = AsyncMock(
            side_effect=PolymarketAPIError(msg="Insufficient funds", status_code=400),
        )
        portfolio = LivePortfolio(client, _MAX_POSITION_PCT)
        await portfolio.refresh_balance()

        result = await portfolio.open_position(
            condition_id=_CONDITION_A,
            token_id=_TOKEN_YES,
            outcome="Yes",
            side=Side.BUY,
            price=Decimal("0.50"),
            quantity=Decimal(10),
            timestamp=_TIMESTAMP,
            reason="test",
            edge=Decimal("0.05"),
        )

        assert result is None
        assert len(portfolio.positions) == 0

    @pytest.mark.asyncio
    async def test_open_uses_market_order_by_default(self) -> None:
        """Verify market order type is used by default."""
        client = _mock_client()
        portfolio = LivePortfolio(client, _MAX_POSITION_PCT)
        await portfolio.refresh_balance()

        await portfolio.open_position(
            condition_id=_CONDITION_A,
            token_id=_TOKEN_YES,
            outcome="Yes",
            side=Side.BUY,
            price=Decimal("0.50"),
            quantity=Decimal(10),
            timestamp=_TIMESTAMP,
            reason="test",
            edge=Decimal("0.05"),
        )

        request = client.place_order.call_args[0][0]
        assert request.order_type == "market"

    @pytest.mark.asyncio
    async def test_open_uses_limit_order_when_configured(self) -> None:
        """Verify limit order type when use_market_orders is False."""
        client = _mock_client()
        portfolio = LivePortfolio(client, _MAX_POSITION_PCT, use_market_orders=False)
        await portfolio.refresh_balance()

        await portfolio.open_position(
            condition_id=_CONDITION_A,
            token_id=_TOKEN_YES,
            outcome="Yes",
            side=Side.BUY,
            price=Decimal("0.50"),
            quantity=Decimal(10),
            timestamp=_TIMESTAMP,
            reason="test",
            edge=Decimal("0.05"),
        )

        request = client.place_order.call_args[0][0]
        assert request.order_type == "limit"


class TestClosePosition:
    """Tests for closing live positions."""

    @pytest.mark.asyncio
    async def test_close_position_places_sell_order(self) -> None:
        """Verify close_position places a SELL order and records a LiveTrade."""
        client = _mock_client()
        portfolio = LivePortfolio(client, _MAX_POSITION_PCT)
        await portfolio.refresh_balance()

        await portfolio.open_position(
            condition_id=_CONDITION_A,
            token_id=_TOKEN_YES,
            outcome="Yes",
            side=Side.BUY,
            price=Decimal("0.50"),
            quantity=Decimal(10),
            timestamp=_TIMESTAMP,
            reason="buy",
            edge=Decimal("0.05"),
        )

        trade = await portfolio.close_position(
            _CONDITION_A,
            _TOKEN_YES,
            Decimal("0.70"),
            Decimal(10),
            _TIMESTAMP + 100,
        )

        assert trade is not None
        assert trade.side == Side.SELL
        assert trade.order_id == _ORDER_ID
        assert _CONDITION_A not in portfolio.positions

    @pytest.mark.asyncio
    async def test_close_nonexistent_returns_none(self) -> None:
        """Verify closing a nonexistent position returns None."""
        client = _mock_client()
        portfolio = LivePortfolio(client, _MAX_POSITION_PCT)

        result = await portfolio.close_position(
            "nonexistent",
            _TOKEN_YES,
            Decimal("0.50"),
            Decimal(10),
            _TIMESTAMP,
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_close_api_error_returns_none(self) -> None:
        """Verify API error during close returns None and keeps position."""
        client = _mock_client()
        portfolio = LivePortfolio(client, _MAX_POSITION_PCT)
        await portfolio.refresh_balance()

        await portfolio.open_position(
            condition_id=_CONDITION_A,
            token_id=_TOKEN_YES,
            outcome="Yes",
            side=Side.BUY,
            price=Decimal("0.50"),
            quantity=Decimal(10),
            timestamp=_TIMESTAMP,
            reason="buy",
            edge=Decimal("0.05"),
        )

        # Make close fail
        client.place_order = AsyncMock(
            side_effect=PolymarketAPIError(msg="Network error", status_code=500),
        )

        result = await portfolio.close_position(
            _CONDITION_A,
            _TOKEN_YES,
            Decimal("0.70"),
            Decimal(10),
            _TIMESTAMP + 100,
        )

        assert result is None
        assert _CONDITION_A in portfolio.positions


class TestMarkToMarket:
    """Tests for mark-to-market valuation."""

    @pytest.mark.asyncio
    async def test_mark_to_market_updates_equity(self) -> None:
        """Verify MTM updates reflected in total_equity."""
        client = _mock_client()
        portfolio = LivePortfolio(client, _MAX_POSITION_PCT)
        await portfolio.refresh_balance()

        await portfolio.open_position(
            condition_id=_CONDITION_A,
            token_id=_TOKEN_YES,
            outcome="Yes",
            side=Side.BUY,
            price=Decimal("0.50"),
            quantity=Decimal(100),
            timestamp=_TIMESTAMP,
            reason="buy",
            edge=Decimal("0.05"),
        )

        portfolio.mark_to_market(_CONDITION_A, Decimal("0.60"))

        # equity = balance + unrealised + position cost
        # = 1000 + (0.60 - 0.50) * 100 + 0.50 * 100
        expected = Decimal(1000) + Decimal(10) + Decimal(50)
        assert portfolio.total_equity == expected

    @pytest.mark.asyncio
    async def test_mark_to_market_ignores_unknown(self) -> None:
        """Verify MTM on unknown condition_id is a no-op."""
        client = _mock_client()
        portfolio = LivePortfolio(client, _MAX_POSITION_PCT)
        await portfolio.refresh_balance()

        portfolio.mark_to_market("unknown", Decimal("0.50"))
        assert portfolio.total_equity == _INITIAL_BALANCE


class TestMaxQuantity:
    """Tests for max_quantity_for helper."""

    @pytest.mark.asyncio
    async def test_max_quantity_respects_allocation(self) -> None:
        """Verify max quantity respects per-market allocation limit."""
        client = _mock_client()
        portfolio = LivePortfolio(client, _MAX_POSITION_PCT)
        await portfolio.refresh_balance()

        # max allocation = 1000 * 0.1 = 100; at price 0.50, qty = 200
        expected_qty = Decimal(200)
        qty = portfolio.max_quantity_for(Decimal("0.50"))
        assert qty == expected_qty

    @pytest.mark.asyncio
    async def test_max_quantity_zero_price(self) -> None:
        """Verify zero price returns zero quantity."""
        client = _mock_client()
        portfolio = LivePortfolio(client, _MAX_POSITION_PCT)
        await portfolio.refresh_balance()

        assert portfolio.max_quantity_for(ZERO) == ZERO


class TestProperties:
    """Tests for portfolio properties."""

    @pytest.mark.asyncio
    async def test_initial_balance(self) -> None:
        """Verify initial balance after refresh."""
        client = _mock_client()
        portfolio = LivePortfolio(client, _MAX_POSITION_PCT)
        await portfolio.refresh_balance()

        assert portfolio.balance == _INITIAL_BALANCE

    def test_empty_positions(self) -> None:
        """Verify empty portfolio has no positions."""
        client = _mock_client()
        portfolio = LivePortfolio(client, _MAX_POSITION_PCT)
        assert portfolio.positions == {}

    def test_empty_trades(self) -> None:
        """Verify empty portfolio has no trades."""
        client = _mock_client()
        portfolio = LivePortfolio(client, _MAX_POSITION_PCT)
        assert portfolio.trades == []

    @pytest.mark.asyncio
    async def test_get_token_id(self) -> None:
        """Verify get_token_id returns cached token ID after open."""
        client = _mock_client()
        portfolio = LivePortfolio(client, _MAX_POSITION_PCT)
        await portfolio.refresh_balance()

        await portfolio.open_position(
            condition_id=_CONDITION_A,
            token_id=_TOKEN_YES,
            outcome="Yes",
            side=Side.BUY,
            price=Decimal("0.50"),
            quantity=Decimal(10),
            timestamp=_TIMESTAMP,
            reason="test",
            edge=Decimal("0.05"),
        )

        assert portfolio.get_token_id(_CONDITION_A) == _TOKEN_YES
        assert portfolio.get_token_id("unknown") is None

    @pytest.mark.asyncio
    async def test_wallet_balance_after_refresh(self) -> None:
        """Verify wallet_balance is populated after refresh."""
        client = _mock_client()
        portfolio = LivePortfolio(client, _MAX_POSITION_PCT)
        await portfolio.refresh_balance()

        assert portfolio.wallet_balance == _WALLET_BALANCE

    @pytest.mark.asyncio
    async def test_wallet_balance_failure_keeps_last_known(self) -> None:
        """Verify wallet balance failure preserves the last known value."""
        client = _mock_client()
        portfolio = LivePortfolio(client, _MAX_POSITION_PCT)

        # First call succeeds
        await portfolio.refresh_balance()
        assert portfolio.wallet_balance == _WALLET_BALANCE

        # Second call fails — should keep the old wallet balance
        client.get_wallet_balance = AsyncMock(side_effect=Exception("RPC down"))
        await portfolio.refresh_balance()
        assert portfolio.wallet_balance == _WALLET_BALANCE

    @pytest.mark.asyncio
    async def test_wallet_balance_failure_does_not_affect_clob_balance(self) -> None:
        """Verify on-chain failure does not prevent CLOB balance from updating."""
        client = _mock_client()
        client.get_wallet_balance = AsyncMock(side_effect=Exception("RPC down"))
        portfolio = LivePortfolio(client, _MAX_POSITION_PCT)

        await portfolio.refresh_balance()

        assert portfolio.balance == _INITIAL_BALANCE
        assert portfolio.wallet_balance == ZERO
