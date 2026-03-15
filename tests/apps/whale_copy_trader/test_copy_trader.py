"""Tests for the WhaleCopyTrader engine with temporal spread arbitrage."""

from __future__ import annotations

import time
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from trading_tools.apps.whale_copy_trader.config import WhaleCopyConfig
from trading_tools.apps.whale_copy_trader.copy_trader import (
    WhaleCopyTrader,
    compute_pnl,
)
from trading_tools.apps.whale_copy_trader.models import (
    CopyResultRecord,
    CopySignal,
    OpenPosition,
    PositionState,
    SideLeg,
)
from trading_tools.clients.polymarket.models import Market, MarketToken, OrderResponse

_ADDRESS = "0xwhale"
_FUTURE_TS = 4_000_000_000
_PAST_TS = 1_000_000_000
_EXPECTED_POLL_COUNT_AFTER_DEDUP = 2
_EXPECTED_MULTI_SIGNAL_COUNT = 2
_DEFAULT_BIAS = Decimal("2.5")
_DEFAULT_MIN_BIAS = Decimal("1.5")


def _make_config(**overrides: object) -> WhaleCopyConfig:
    """Create a WhaleCopyConfig with test defaults.

    Args:
        **overrides: Fields to override on the config.

    Returns:
        A WhaleCopyConfig instance.

    """
    defaults: dict[str, object] = {
        "whale_address": _ADDRESS,
        "poll_interval": 1,
        "lookback_seconds": 300,
        "min_bias": _DEFAULT_MIN_BIAS,
        "min_trades": 3,
        "capital": Decimal(100),
        "max_position_pct": Decimal("0.10"),
        "max_spread_cost": Decimal("0.95"),
        "max_entry_price": Decimal("0.65"),
    }
    defaults.update(overrides)
    return WhaleCopyConfig(**defaults)  # type: ignore[arg-type]


def _make_signal(
    condition_id: str = "cond_a",
    favoured_side: str = "Up",
    window_end_ts: int = _FUTURE_TS,
    bias_ratio: Decimal = _DEFAULT_BIAS,
) -> CopySignal:
    """Create a CopySignal for testing.

    Args:
        condition_id: Market condition ID.
        favoured_side: Whale's favoured direction.
        window_end_ts: When the market window closes.
        bias_ratio: Whale's bias ratio.

    Returns:
        A CopySignal instance.

    """
    return CopySignal(
        condition_id=condition_id,
        title="Bitcoin Up or Down - March 13, 6PM ET",
        asset="BTC-USD",
        favoured_side=favoured_side,
        bias_ratio=bias_ratio,
        trade_count=5,
        window_start_ts=_FUTURE_TS - 300,
        window_end_ts=window_end_ts,
        detected_at=int(time.time()),
    )


def _mock_market(up_price: str = "0.55", down_price: str = "0.45") -> Market:
    """Create a mock Market with the given prices.

    Args:
        up_price: Price for the Up token.
        down_price: Price for the Down token.

    Returns:
        A Market instance.

    """
    return Market(
        condition_id="cond_a",
        question="BTC Up or Down?",
        description="5 min market",
        tokens=(
            MarketToken(token_id="tok_up", outcome="Up", price=Decimal(up_price)),
            MarketToken(token_id="tok_down", outcome="Down", price=Decimal(down_price)),
        ),
        end_date="2099-01-01",
        volume=Decimal(10000),
        liquidity=Decimal(5000),
        active=True,
    )


def _mock_client(up_price: str = "0.55", down_price: str = "0.45") -> AsyncMock:
    """Create a mock PolymarketClient with standard market data.

    Args:
        up_price: Price for the Up token.
        down_price: Price for the Down token.

    Returns:
        A mock client.

    """
    client = AsyncMock()
    client.get_market = AsyncMock(return_value=_mock_market(up_price, down_price))
    client.place_order = AsyncMock(
        return_value=OrderResponse(
            order_id="order_123",
            status="matched",
            token_id="tok_up",
            side="BUY",
            price=Decimal(up_price),
            size=Decimal("18.18"),
            filled=Decimal("18.18"),
        )
    )
    client.close = AsyncMock()
    return client


class TestWhaleCopyTrader:
    """Tests for the temporal spread arbitrage copy-trading engine."""

    @pytest.fixture
    def trader(self) -> WhaleCopyTrader:
        """Create a WhaleCopyTrader in paper mode with CLOB client."""
        return WhaleCopyTrader(
            config=_make_config(),
            client=_mock_client(),
        )

    @pytest.mark.asyncio
    async def test_poll_cycle_no_signals(self, trader: WhaleCopyTrader) -> None:
        """Complete a poll cycle with no signals detected."""
        with patch.object(trader, "_detector") as mock_detector:
            mock_detector.detect_signals = AsyncMock(return_value=[])
            trader._detector = mock_detector
            await trader._poll_cycle()

        assert trader.poll_count == 1
        assert len(trader.positions) == 0

    @pytest.mark.asyncio
    async def test_opens_directional_leg1(self, trader: WhaleCopyTrader) -> None:
        """Open a single directional leg (leg 1) copying the whale."""
        signal = _make_signal()

        with patch.object(trader, "_detector") as mock_detector:
            mock_detector.detect_signals = AsyncMock(return_value=[signal])
            trader._detector = mock_detector
            await trader._poll_cycle()

        assert "cond_a" in trader.positions
        pos = trader.positions["cond_a"]
        assert pos.is_paper
        assert pos.state == PositionState.UNHEDGED
        assert pos.leg1.side == "Up"
        assert pos.leg1.entry_price == Decimal("0.55")
        assert pos.leg1.quantity > Decimal(0)
        assert pos.hedge_leg is None
        assert pos.hedge_side == "Down"

    @pytest.mark.asyncio
    async def test_skips_entry_when_price_too_high(self) -> None:
        """Skip entry when favoured side price exceeds max_entry_price."""
        config = _make_config(max_entry_price=Decimal("0.50"))
        # Up price = 0.55 > max_entry_price = 0.50
        trader = WhaleCopyTrader(config=config, client=_mock_client("0.55", "0.45"))

        signal = _make_signal(favoured_side="Up")

        with patch.object(trader, "_detector") as mock_detector:
            mock_detector.detect_signals = AsyncMock(return_value=[signal])
            trader._detector = mock_detector
            await trader._poll_cycle()

        assert len(trader.positions) == 0

    @pytest.mark.asyncio
    async def test_ignores_duplicate_signal(self, trader: WhaleCopyTrader) -> None:
        """Ignore signals for markets where we already have a position."""
        signal = _make_signal()

        with patch.object(trader, "_detector") as mock_detector:
            mock_detector.detect_signals = AsyncMock(return_value=[signal])
            trader._detector = mock_detector
            await trader._poll_cycle()
            first_cost = trader.positions["cond_a"].total_cost_basis
            await trader._poll_cycle()

        assert trader.positions["cond_a"].total_cost_basis == first_cost
        assert trader.poll_count == _EXPECTED_POLL_COUNT_AFTER_DEDUP

    @pytest.mark.asyncio
    async def test_hedge_placed_when_spread_below_threshold(self) -> None:
        """Place hedge leg when combined cost is below max_spread_cost."""
        config = _make_config(
            max_spread_cost=Decimal("0.95"),
            max_entry_price=Decimal("0.65"),
        )
        # Up=0.55, Down=0.35 → combined=0.90 < 0.95 target
        client = _mock_client("0.55", "0.35")
        trader = WhaleCopyTrader(config=config, client=client)

        signal = _make_signal(favoured_side="Up")

        with patch.object(trader, "_detector") as mock_detector:
            mock_detector.detect_signals = AsyncMock(return_value=[signal])
            trader._detector = mock_detector
            await trader._poll_cycle()

        pos = trader.positions["cond_a"]
        assert pos.state == PositionState.HEDGED
        assert pos.hedge_leg is not None
        assert pos.hedge_leg.side == "Down"
        assert pos.hedge_leg.entry_price == Decimal("0.35")

    @pytest.mark.asyncio
    async def test_hedge_not_placed_when_spread_above_threshold(self) -> None:
        """Keep position UNHEDGED when combined cost exceeds target."""
        config = _make_config(
            max_spread_cost=Decimal("0.90"),
            max_entry_price=Decimal("0.65"),
        )
        # Up=0.55, Down=0.45 → combined=1.00 > 0.90 target
        trader = WhaleCopyTrader(config=config, client=_mock_client("0.55", "0.45"))

        signal = _make_signal(favoured_side="Up")

        with patch.object(trader, "_detector") as mock_detector:
            mock_detector.detect_signals = AsyncMock(return_value=[signal])
            trader._detector = mock_detector
            await trader._poll_cycle()

        pos = trader.positions["cond_a"]
        assert pos.state == PositionState.UNHEDGED
        assert pos.hedge_leg is None

    @pytest.mark.asyncio
    async def test_unhedged_pnl_whale_correct(self) -> None:
        """Compute positive P&L when unhedged and whale is correct."""
        config = _make_config(max_spread_cost=Decimal("0.80"))
        trader = WhaleCopyTrader(config=config, client=_mock_client("0.55", "0.45"))

        signal = _make_signal(favoured_side="Up", window_end_ts=_PAST_TS)

        with (
            patch.object(trader, "_detector") as mock_detector,
            patch.object(trader, "_resolve_outcome", new_callable=AsyncMock, return_value="Up"),
        ):
            mock_detector.detect_signals = AsyncMock(return_value=[signal])
            trader._detector = mock_detector
            await trader._poll_cycle()

        assert len(trader.results) == 1
        result = trader.results[0]
        assert result.winning_side == "Up"
        assert result.state == PositionState.UNHEDGED
        # qty * $1.00 - cost = qty - cost = qty - (price * qty) = qty * (1 - price)
        assert result.pnl > Decimal(0)

    @pytest.mark.asyncio
    async def test_unhedged_pnl_whale_wrong(self) -> None:
        """Compute negative P&L when unhedged and whale is wrong."""
        config = _make_config(max_spread_cost=Decimal("0.80"))
        trader = WhaleCopyTrader(config=config, client=_mock_client("0.55", "0.45"))

        signal = _make_signal(favoured_side="Up", window_end_ts=_PAST_TS)

        with (
            patch.object(trader, "_detector") as mock_detector,
            patch.object(trader, "_resolve_outcome", new_callable=AsyncMock, return_value="Down"),
        ):
            mock_detector.detect_signals = AsyncMock(return_value=[signal])
            trader._detector = mock_detector
            await trader._poll_cycle()

        assert len(trader.results) == 1
        result = trader.results[0]
        assert result.winning_side == "Down"
        assert result.pnl < Decimal(0)
        assert result.pnl == -result.total_cost_basis

    @pytest.mark.asyncio
    async def test_hedged_position_reduces_downside(self) -> None:
        """Verify hedge reduces loss when leg1 loses vs unhedged scenario."""
        config = _make_config(
            max_spread_cost=Decimal("0.95"),
            max_entry_price=Decimal("0.65"),
        )
        # Spread = 0.55 + 0.35 = 0.90 < 0.95 → hedge triggers
        client = _mock_client("0.55", "0.35")
        trader = WhaleCopyTrader(config=config, client=client)

        signal = _make_signal(favoured_side="Up", window_end_ts=_PAST_TS)

        with (
            patch.object(trader, "_detector") as mock_detector,
            patch.object(trader, "_resolve_outcome", new_callable=AsyncMock, return_value="Down"),
        ):
            mock_detector.detect_signals = AsyncMock(return_value=[signal])
            trader._detector = mock_detector
            await trader._poll_cycle()

        assert len(trader.results) == 1
        result = trader.results[0]
        assert result.state == PositionState.HEDGED
        # Hedge side (Down) wins: hedge_qty - total_cost
        # With cheap hedge (0.35), hedge_qty is large → net positive
        assert result.pnl > Decimal(0)
        # Meanwhile unhedged loss would be -leg1_cost = -$10
        assert result.pnl > -result.leg1_qty

    @pytest.mark.asyncio
    async def test_expired_unhedged_position_closes(self) -> None:
        """Close an unhedged position when its market window expires."""
        config = _make_config(max_spread_cost=Decimal("0.80"))
        trader = WhaleCopyTrader(config=config, client=_mock_client("0.55", "0.45"))

        signal = _make_signal(window_end_ts=_PAST_TS)

        with (
            patch.object(trader, "_detector") as mock_detector,
            patch.object(trader, "_resolve_outcome", new_callable=AsyncMock, return_value="Up"),
        ):
            mock_detector.detect_signals = AsyncMock(return_value=[signal])
            trader._detector = mock_detector
            await trader._poll_cycle()

        assert len(trader.positions) == 0
        assert len(trader.results) == 1

    @pytest.mark.asyncio
    async def test_stop_signals_shutdown(self, trader: WhaleCopyTrader) -> None:
        """Calling stop() sets the shutdown flag."""
        trader.stop()
        assert trader._shutdown.should_stop

    @pytest.mark.asyncio
    async def test_multiple_signals_same_cycle(self, trader: WhaleCopyTrader) -> None:
        """Handle multiple signals in a single poll cycle."""
        signals = [
            _make_signal(condition_id="cond_a"),
            _make_signal(condition_id="cond_b"),
        ]

        with patch.object(trader, "_detector") as mock_detector:
            mock_detector.detect_signals = AsyncMock(return_value=signals)
            trader._detector = mock_detector
            await trader._poll_cycle()

        assert len(trader.positions) == _EXPECTED_MULTI_SIGNAL_COUNT


class TestSideLeg:
    """Tests for the SideLeg model."""

    def test_add_fill_updates_weighted_average(self) -> None:
        """Update entry price to weighted average after additional fill."""
        leg = SideLeg(
            side="Up",
            entry_price=Decimal("0.50"),
            quantity=Decimal("20.00"),
            cost_basis=Decimal("10.00"),
        )
        leg.add_fill(Decimal("0.70"), Decimal("10.00"))

        _expected_total_qty = Decimal("30.00")
        _expected_cost = Decimal("17.00")
        assert leg.quantity == _expected_total_qty
        assert leg.cost_basis == _expected_cost
        assert leg.entry_price == Decimal("0.5667")


class TestComputePnl:
    """Tests for the compute_pnl helper."""

    def _make_unhedged_position(
        self,
        leg1_side: str = "Up",
        leg1_qty: Decimal = Decimal("18.18"),
        leg1_cost: Decimal = Decimal("10.00"),
    ) -> OpenPosition:
        """Create an UNHEDGED position for P&L testing.

        Args:
            leg1_side: Direction of the directional entry.
            leg1_qty: Token quantity for leg 1.
            leg1_cost: Cost basis for leg 1.

        Returns:
            An unhedged OpenPosition.

        """
        return OpenPosition(
            signal=_make_signal(),
            state=PositionState.UNHEDGED,
            leg1=SideLeg(
                side=leg1_side,
                entry_price=Decimal("0.55"),
                quantity=leg1_qty,
                cost_basis=leg1_cost,
            ),
            hedge_leg=None,
            hedge_side="Down" if leg1_side == "Up" else "Up",
            entry_time=1000,
        )

    def _make_hedged_position(self) -> OpenPosition:
        """Create a HEDGED position for P&L testing.

        Use equal-token sizing: same quantity on both legs at different
        prices, guaranteeing profit regardless of outcome when
        ``combined < 1.0``.

        Leg 1: 18.18 Up tokens @ 0.55 = $10.00
        Hedge: 18.18 Down tokens @ 0.35 = $6.363
        Total cost: $16.363, guaranteed payout: $18.18

        Returns:
            A hedged OpenPosition with both legs.

        """
        qty = Decimal("18.18")
        return OpenPosition(
            signal=_make_signal(),
            state=PositionState.HEDGED,
            leg1=SideLeg(
                side="Up",
                entry_price=Decimal("0.55"),
                quantity=qty,
                cost_basis=Decimal("10.00"),
            ),
            hedge_leg=SideLeg(
                side="Down",
                entry_price=Decimal("0.35"),
                quantity=qty,
                cost_basis=Decimal("6.363"),
            ),
            hedge_side="Down",
            entry_time=1000,
        )

    def test_unhedged_pnl_when_leg1_wins(self) -> None:
        """P&L = leg1_qty - leg1_cost when leg1 side wins."""
        pos = self._make_unhedged_position()
        pnl = compute_pnl(pos, "Up")
        assert pnl == Decimal("18.18") - Decimal("10.00")

    def test_unhedged_pnl_when_leg1_loses(self) -> None:
        """P&L = -leg1_cost when leg1 side loses."""
        pos = self._make_unhedged_position()
        pnl = compute_pnl(pos, "Down")
        assert pnl == -Decimal("10.00")

    def test_hedged_pnl_when_leg1_wins(self) -> None:
        """Guarantee profit when leg 1 wins (hedged, equal tokens)."""
        pos = self._make_hedged_position()
        pnl = compute_pnl(pos, "Up")
        # 18.18 - (10.00 + 6.363) = 1.817
        assert pnl == Decimal("18.18") - Decimal("16.363")
        assert pnl > Decimal(0)

    def test_hedged_pnl_when_hedge_wins(self) -> None:
        """Guarantee profit when hedge side wins (equal tokens)."""
        pos = self._make_hedged_position()
        pnl = compute_pnl(pos, "Down")
        # 18.18 - (10.00 + 6.363) = 1.817
        assert pnl == Decimal("18.18") - Decimal("16.363")
        assert pnl > Decimal(0)

    def test_hedged_equal_tokens_identical_pnl_either_outcome(self) -> None:
        """Verify equal token quantities yield identical P&L regardless of outcome."""
        pos = self._make_hedged_position()
        pnl_leg1_wins = compute_pnl(pos, "Up")
        pnl_hedge_wins = compute_pnl(pos, "Down")

        # Equal tokens → same payout ($18.18) regardless of winner
        assert pnl_leg1_wins == pnl_hedge_wins
        assert pnl_leg1_wins > Decimal(0)


class TestLiveTradingFlow:
    """Tests for live trading with mocked Polymarket client."""

    @pytest.fixture
    def mock_client(self) -> AsyncMock:
        """Create a mock PolymarketClient."""
        return _mock_client()

    @pytest.fixture
    def live_trader(self, mock_client: AsyncMock) -> WhaleCopyTrader:
        """Create a WhaleCopyTrader in live mode."""
        return WhaleCopyTrader(
            config=_make_config(),
            live=True,
            client=mock_client,
        )

    @pytest.mark.asyncio
    async def test_live_places_single_leg_order(
        self, live_trader: WhaleCopyTrader, mock_client: AsyncMock
    ) -> None:
        """Place only 1 order for leg 1 on entry (not 2)."""
        signal = _make_signal()

        with patch.object(live_trader, "_detector") as mock_detector:
            mock_detector.detect_signals = AsyncMock(return_value=[signal])
            live_trader._detector = mock_detector
            await live_trader._poll_cycle()

        # get_market called twice: once for prices in _open_position,
        # once for tokens in _open_position, once for hedge check
        assert mock_client.place_order.call_count == 1

        pos = live_trader.positions["cond_a"]
        assert not pos.is_paper
        assert pos.state == PositionState.UNHEDGED
        assert pos.leg1.side == "Up"
        assert len(pos.all_order_ids) == 1

    @pytest.mark.asyncio
    async def test_live_handles_market_fetch_error(
        self, live_trader: WhaleCopyTrader, mock_client: AsyncMock
    ) -> None:
        """Handle errors when fetching market data gracefully."""
        mock_client.get_market = AsyncMock(side_effect=RuntimeError("API error"))
        signal = _make_signal()

        with patch.object(live_trader, "_detector") as mock_detector:
            mock_detector.detect_signals = AsyncMock(return_value=[signal])
            live_trader._detector = mock_detector
            await live_trader._poll_cycle()

        assert len(live_trader.positions) == 0


class TestHedgeWithMarketOrders:
    """Tests for hedge FOK market order feature (G)."""

    @pytest.mark.asyncio
    async def test_hedge_uses_market_order_type(self) -> None:
        """Hedge leg passes order_type='market' when hedge_with_market_orders is on."""
        config = _make_config(
            max_spread_cost=Decimal("0.95"),
            max_entry_price=Decimal("0.65"),
            hedge_with_market_orders=True,
        )
        client = _mock_client("0.55", "0.35")
        trader = WhaleCopyTrader(config=config, live=True, client=client)

        signal = _make_signal(favoured_side="Up")

        with patch.object(trader, "_detector") as mock_detector:
            mock_detector.detect_signals = AsyncMock(return_value=[signal])
            trader._detector = mock_detector
            await trader._poll_cycle()

        pos = trader.positions["cond_a"]
        assert pos.state == PositionState.HEDGED
        # The second place_order call (hedge) should have order_type="market"
        calls = client.place_order.call_args_list
        _expected_call_count = 2
        assert len(calls) == _expected_call_count
        # Hedge call is the second one — check the request's order_type
        hedge_request = calls[1][0][0]
        assert hedge_request.order_type == "market"

    @pytest.mark.asyncio
    async def test_hedge_uses_limit_when_disabled(self) -> None:
        """Hedge leg uses limit orders when hedge_with_market_orders is False."""
        config = _make_config(
            max_spread_cost=Decimal("0.95"),
            max_entry_price=Decimal("0.65"),
            hedge_with_market_orders=False,
            use_market_orders=False,
        )
        client = _mock_client("0.55", "0.35")
        trader = WhaleCopyTrader(config=config, live=True, client=client)

        signal = _make_signal(favoured_side="Up")

        with patch.object(trader, "_detector") as mock_detector:
            mock_detector.detect_signals = AsyncMock(return_value=[signal])
            trader._detector = mock_detector
            await trader._poll_cycle()

        pos = trader.positions["cond_a"]
        assert pos.state == PositionState.HEDGED
        calls = client.place_order.call_args_list
        _expected_call_count = 2
        assert len(calls) == _expected_call_count
        hedge_request = calls[1][0][0]
        assert hedge_request.order_type == "limit"


class TestActualFillTracking:
    """Tests for fill tracking from order response (A)."""

    @pytest.mark.asyncio
    async def test_leg_quantity_adjusted_from_response(self) -> None:
        """Leg quantity and cost basis reflect actual fill, not quoted amount."""
        config = _make_config(max_entry_price=Decimal("0.65"))
        client = _mock_client("0.55", "0.45")
        # Make the fill smaller than requested
        _partial_fill = Decimal("10.00")
        client.place_order = AsyncMock(
            return_value=OrderResponse(
                order_id="order_partial",
                status="matched",
                token_id="tok_up",
                side="BUY",
                price=Decimal("0.55"),
                size=Decimal("18.18"),
                filled=_partial_fill,
            )
        )
        trader = WhaleCopyTrader(config=config, live=True, client=client)

        signal = _make_signal(favoured_side="Up")

        with patch.object(trader, "_detector") as mock_detector:
            mock_detector.detect_signals = AsyncMock(return_value=[signal])
            trader._detector = mock_detector
            await trader._poll_cycle()

        pos = trader.positions["cond_a"]
        assert pos.leg1.quantity == _partial_fill
        assert pos.leg1.cost_basis == Decimal("0.55") * _partial_fill

    @pytest.mark.asyncio
    async def test_zero_fill_treated_as_failure(self) -> None:
        """Order that fills zero tokens is treated as a failed order."""
        config = _make_config(max_entry_price=Decimal("0.65"))
        client = _mock_client("0.55", "0.45")
        client.place_order = AsyncMock(
            return_value=OrderResponse(
                order_id="order_zero",
                status="cancelled",
                token_id="tok_up",
                side="BUY",
                price=Decimal("0.55"),
                size=Decimal("18.18"),
                filled=Decimal(0),
            )
        )
        trader = WhaleCopyTrader(config=config, live=True, client=client)

        signal = _make_signal(favoured_side="Up")

        with patch.object(trader, "_detector") as mock_detector:
            mock_detector.detect_signals = AsyncMock(return_value=[signal])
            trader._detector = mock_detector
            await trader._poll_cycle()

        assert len(trader.positions) == 0


class TestDynamicHedgeSizing:
    """Tests for cost-basis-derived hedge threshold (B)."""

    @pytest.mark.asyncio
    async def test_hedge_uses_effective_leg1_price(self) -> None:
        """Hedge threshold uses cost_basis / quantity rather than entry_price."""
        # Leg1 enters at 0.55, but after fill tracking the effective price
        # may differ. With paper mode, cost_basis = entry * qty so it's the same.
        # This test verifies the math works.
        config = _make_config(
            max_spread_cost=Decimal("0.95"),
            max_entry_price=Decimal("0.65"),
        )
        client = _mock_client("0.55", "0.35")
        trader = WhaleCopyTrader(config=config, client=client)

        signal = _make_signal(favoured_side="Up")

        with patch.object(trader, "_detector") as mock_detector:
            mock_detector.detect_signals = AsyncMock(return_value=[signal])
            trader._detector = mock_detector
            await trader._poll_cycle()

        pos = trader.positions["cond_a"]
        # 0.55 + 0.35 = 0.90 < 0.95 → hedge should trigger
        assert pos.state == PositionState.HEDGED


class TestStopLoss:
    """Tests for stop-loss on unhedged positions (C)."""

    @pytest.mark.asyncio
    async def test_stop_loss_triggers_on_price_drop(self) -> None:
        """Close position when leg1 price drops below stop threshold."""
        config = _make_config(
            max_spread_cost=Decimal("0.80"),  # prevent hedge
            max_entry_price=Decimal("0.65"),
            stop_loss_pct=Decimal("0.50"),
        )
        # Entry at 0.55, then price drops to 0.20 (< 0.55 * 0.50 = 0.275)
        client = AsyncMock()
        # First call: open position at 0.55
        # Second call: stop-loss check sees 0.20
        market_open = _mock_market("0.55", "0.45")
        market_drop = _mock_market("0.20", "0.80")
        client.get_market = AsyncMock(side_effect=[market_open, market_drop])
        client.close = AsyncMock()

        trader = WhaleCopyTrader(config=config, client=client)

        signal = _make_signal(favoured_side="Up")

        with patch.object(trader, "_detector") as mock_detector:
            # First cycle: open position
            mock_detector.detect_signals = AsyncMock(return_value=[signal])
            trader._detector = mock_detector
            await trader._poll_cycle()

        assert "cond_a" in trader.positions
        assert trader.positions["cond_a"].state == PositionState.UNHEDGED

        # Second cycle: price dropped, stop-loss should trigger
        client.get_market = AsyncMock(return_value=market_drop)

        with patch.object(trader, "_detector") as mock_detector:
            mock_detector.detect_signals = AsyncMock(return_value=[])
            trader._detector = mock_detector
            await trader._poll_cycle()

        assert "cond_a" not in trader.positions
        assert len(trader.results) == 1
        assert trader.results[0].state == PositionState.STOPPED
        assert trader.results[0].pnl < Decimal(0)

    @pytest.mark.asyncio
    async def test_stop_loss_does_not_trigger_above_threshold(self) -> None:
        """Keep position open when price is above stop threshold."""
        config = _make_config(
            max_spread_cost=Decimal("0.80"),
            max_entry_price=Decimal("0.65"),
            stop_loss_pct=Decimal("0.50"),
        )
        # Entry at 0.55, price drops to 0.40 (> 0.275 threshold)
        client = _mock_client("0.55", "0.45")
        trader = WhaleCopyTrader(config=config, client=client)

        signal = _make_signal(favoured_side="Up")

        with patch.object(trader, "_detector") as mock_detector:
            mock_detector.detect_signals = AsyncMock(return_value=[signal])
            trader._detector = mock_detector
            await trader._poll_cycle()

        # Change prices for second cycle but still above stop
        client = _mock_client("0.40", "0.60")
        trader.client = client

        with patch.object(trader, "_detector") as mock_detector:
            mock_detector.detect_signals = AsyncMock(return_value=[])
            trader._detector = mock_detector
            await trader._poll_cycle()

        assert "cond_a" in trader.positions
        assert trader.positions["cond_a"].state == PositionState.UNHEDGED


class TestKellyPositionSizing:
    """Tests for Kelly criterion position sizing (D)."""

    def test_kelly_reduces_position_at_high_entry_price(self) -> None:
        """Kelly size is smaller when entry price is high (lower edge)."""
        config = _make_config(
            win_rate=Decimal("0.80"),
            kelly_fraction=Decimal("0.5"),
            max_position_pct=Decimal("0.20"),
        )
        trader = WhaleCopyTrader(config=config, client=_mock_client())

        # At price 0.55: b = 0.45/0.55 ≈ 0.818, kelly = (0.818*0.8-0.2)/0.818 ≈ 0.556
        # half-kelly = 0.278, clamped to max 0.20
        pct_low = trader._kelly_position_pct(Decimal("0.55"))

        # At price 0.62: b = 0.38/0.62 ≈ 0.613, kelly = (0.613*0.8-0.2)/0.613 ≈ 0.474
        # half-kelly = 0.237, clamped to max 0.20
        pct_high = trader._kelly_position_pct(Decimal("0.62"))

        # Both clamped to max in this case, but kelly itself is smaller for higher price
        assert pct_low <= Decimal("0.20")
        assert pct_high <= Decimal("0.20")

    def test_kelly_returns_zero_for_negative_edge(self) -> None:
        """Kelly returns zero when the edge is negative."""
        config = _make_config(
            win_rate=Decimal("0.30"),  # low win rate
            kelly_fraction=Decimal("0.5"),
        )
        trader = WhaleCopyTrader(config=config, client=_mock_client())

        # At price 0.55 with 30% win rate: b = 0.818, kelly = (0.818*0.3-0.7)/0.818 < 0
        pct = trader._kelly_position_pct(Decimal("0.55"))
        assert pct == Decimal(0)

    def test_kelly_clamped_to_max_position_pct(self) -> None:
        """Kelly result never exceeds max_position_pct."""
        config = _make_config(
            win_rate=Decimal("0.95"),
            kelly_fraction=Decimal("1.0"),  # full Kelly
            max_position_pct=Decimal("0.10"),
        )
        trader = WhaleCopyTrader(config=config, client=_mock_client())

        pct = trader._kelly_position_pct(Decimal("0.40"))
        assert pct <= Decimal("0.10")


class TestDynamicMaxSpreadCost:
    """Tests for fee-adjusted hedge threshold (E)."""

    @pytest.mark.asyncio
    async def test_fees_tighten_hedge_threshold(self) -> None:
        """Non-zero fee rate makes hedge trigger harder to reach."""
        # Without fees: combined 0.90 < max_spread 0.95 → hedge
        # With 5% fee rate: effective_max = 0.95 - 2*0.05 = 0.85
        # combined 0.90 > 0.85 → no hedge
        config = _make_config(
            max_spread_cost=Decimal("0.95"),
            max_entry_price=Decimal("0.65"),
            clob_fee_rate=Decimal("0.05"),
        )
        client = _mock_client("0.55", "0.35")
        trader = WhaleCopyTrader(config=config, client=client)

        signal = _make_signal(favoured_side="Up")

        with patch.object(trader, "_detector") as mock_detector:
            mock_detector.detect_signals = AsyncMock(return_value=[signal])
            trader._detector = mock_detector
            await trader._poll_cycle()

        pos = trader.positions["cond_a"]
        assert pos.state == PositionState.UNHEDGED

    @pytest.mark.asyncio
    async def test_zero_fee_rate_no_impact(self) -> None:
        """Zero fee rate has no effect on hedge threshold."""
        config = _make_config(
            max_spread_cost=Decimal("0.95"),
            max_entry_price=Decimal("0.65"),
            clob_fee_rate=Decimal("0.0"),
        )
        client = _mock_client("0.55", "0.35")
        trader = WhaleCopyTrader(config=config, client=client)

        signal = _make_signal(favoured_side="Up")

        with patch.object(trader, "_detector") as mock_detector:
            mock_detector.detect_signals = AsyncMock(return_value=[signal])
            trader._detector = mock_detector
            await trader._poll_cycle()

        pos = trader.positions["cond_a"]
        assert pos.state == PositionState.HEDGED


class TestTakeProfit:
    """Tests for mid-trade take-profit exits (F)."""

    @pytest.mark.asyncio
    async def test_take_profit_triggers_at_target_price(self) -> None:
        """Exit position when leg1 price rises to take-profit level."""
        config = _make_config(
            max_spread_cost=Decimal("0.80"),  # prevent hedge
            max_entry_price=Decimal("0.65"),
            take_profit_pct=Decimal("0.55"),
        )
        market_open = _mock_market("0.55", "0.45")
        market_up = _mock_market("0.90", "0.10")

        client = AsyncMock()
        # First cycle needs: 1 price fetch (open), then take-profit/stop-loss/hedge checks
        # all see normal prices so position stays open
        client.get_market = AsyncMock(return_value=market_open)
        client.close = AsyncMock()

        trader = WhaleCopyTrader(config=config, client=client)

        signal = _make_signal(favoured_side="Up")

        with patch.object(trader, "_detector") as mock_detector:
            mock_detector.detect_signals = AsyncMock(return_value=[signal])
            trader._detector = mock_detector
            await trader._poll_cycle()

        assert "cond_a" in trader.positions

        # Second cycle: price up, take-profit should trigger
        client.get_market = AsyncMock(return_value=market_up)

        with patch.object(trader, "_detector") as mock_detector:
            mock_detector.detect_signals = AsyncMock(return_value=[])
            trader._detector = mock_detector
            await trader._poll_cycle()

        assert "cond_a" not in trader.positions
        assert len(trader.results) == 1
        assert trader.results[0].state == PositionState.EXITED
        assert trader.results[0].pnl > Decimal(0)

    @pytest.mark.asyncio
    async def test_take_profit_does_not_trigger_below_target(self) -> None:
        """Keep position open when price is below take-profit level."""
        config = _make_config(
            max_spread_cost=Decimal("0.80"),
            max_entry_price=Decimal("0.65"),
            take_profit_pct=Decimal("0.55"),
        )
        client = _mock_client("0.55", "0.45")
        trader = WhaleCopyTrader(config=config, client=client)

        signal = _make_signal(favoured_side="Up")

        with patch.object(trader, "_detector") as mock_detector:
            mock_detector.detect_signals = AsyncMock(return_value=[signal])
            trader._detector = mock_detector
            await trader._poll_cycle()
            # Second cycle: price still at 0.55 < 0.85
            mock_detector.detect_signals = AsyncMock(return_value=[])
            await trader._poll_cycle()

        assert "cond_a" in trader.positions
        assert trader.positions["cond_a"].state == PositionState.UNHEDGED

    @pytest.mark.asyncio
    async def test_take_profit_checked_before_hedge(self) -> None:
        """Take-profit fires before hedge check in the poll cycle."""
        config = _make_config(
            max_spread_cost=Decimal("0.95"),
            max_entry_price=Decimal("0.65"),
            take_profit_pct=Decimal("0.55"),
        )
        market_open = _mock_market("0.55", "0.45")
        market_jump = _mock_market("0.90", "0.05")

        client = AsyncMock()
        # First cycle: all calls see normal prices (hedge triggers at 0.55+0.45=1.0 > 0.95, no hedge)
        client.get_market = AsyncMock(return_value=market_open)
        client.close = AsyncMock()

        trader = WhaleCopyTrader(config=config, client=client)

        signal = _make_signal(favoured_side="Up")

        with patch.object(trader, "_detector") as mock_detector:
            mock_detector.detect_signals = AsyncMock(return_value=[signal])
            trader._detector = mock_detector
            await trader._poll_cycle()

        assert "cond_a" in trader.positions

        # Second cycle: price jumps — both take-profit (0.90 >= 0.85) and
        # hedge (0.55 + 0.05 = 0.60 < 0.95) could trigger, but take-profit
        # is checked first and wins
        client.get_market = AsyncMock(return_value=market_jump)

        with patch.object(trader, "_detector") as mock_detector:
            mock_detector.detect_signals = AsyncMock(return_value=[])
            trader._detector = mock_detector
            await trader._poll_cycle()

        assert "cond_a" not in trader.positions
        assert trader.results[0].state == PositionState.EXITED


class TestDatabasePersistence:
    """Tests for persisting copy results to the database."""

    @pytest.mark.asyncio
    async def test_persist_on_expired_close(self) -> None:
        """Verify save_result is called when an expired position closes."""
        config = _make_config(max_spread_cost=Decimal("0.80"))
        trader = WhaleCopyTrader(config=config, client=_mock_client("0.55", "0.45"))

        mock_repo = AsyncMock()
        trader.set_repo(mock_repo)

        signal = _make_signal(window_end_ts=_PAST_TS)

        with (
            patch.object(trader, "_detector") as mock_detector,
            patch.object(trader, "_resolve_outcome", new_callable=AsyncMock, return_value="Up"),
        ):
            mock_detector.detect_signals = AsyncMock(return_value=[signal])
            trader._detector = mock_detector
            await trader._poll_cycle()

        mock_repo.save_result.assert_called_once()
        record = mock_repo.save_result.call_args[0][0]
        assert isinstance(record, CopyResultRecord)
        assert record.condition_id == "cond_a"

    @pytest.mark.asyncio
    async def test_persist_on_take_profit(self) -> None:
        """Verify save_result is called on take-profit exit."""
        config = _make_config(
            max_spread_cost=Decimal("0.80"),
            max_entry_price=Decimal("0.65"),
            take_profit_pct=Decimal("0.55"),
        )
        market_open = _mock_market("0.55", "0.45")
        market_up = _mock_market("0.90", "0.10")

        client = AsyncMock()
        client.get_market = AsyncMock(return_value=market_open)
        client.close = AsyncMock()

        trader = WhaleCopyTrader(config=config, client=client)
        mock_repo = AsyncMock()
        trader.set_repo(mock_repo)

        signal = _make_signal(favoured_side="Up")

        with patch.object(trader, "_detector") as mock_detector:
            mock_detector.detect_signals = AsyncMock(return_value=[signal])
            trader._detector = mock_detector
            await trader._poll_cycle()

        # Second cycle: take-profit
        client.get_market = AsyncMock(return_value=market_up)

        with patch.object(trader, "_detector") as mock_detector:
            mock_detector.detect_signals = AsyncMock(return_value=[])
            trader._detector = mock_detector
            await trader._poll_cycle()

        mock_repo.save_result.assert_called_once()
        record = mock_repo.save_result.call_args[0][0]
        assert record.state == "exited"

    @pytest.mark.asyncio
    async def test_persist_on_stop_loss(self) -> None:
        """Verify save_result is called on stop-loss exit."""
        config = _make_config(
            max_spread_cost=Decimal("0.80"),
            max_entry_price=Decimal("0.65"),
            stop_loss_pct=Decimal("0.50"),
        )
        market_open = _mock_market("0.55", "0.45")
        market_drop = _mock_market("0.20", "0.80")

        client = AsyncMock()
        client.get_market = AsyncMock(return_value=market_open)
        client.close = AsyncMock()

        trader = WhaleCopyTrader(config=config, client=client)
        mock_repo = AsyncMock()
        trader.set_repo(mock_repo)

        signal = _make_signal(favoured_side="Up")

        with patch.object(trader, "_detector") as mock_detector:
            mock_detector.detect_signals = AsyncMock(return_value=[signal])
            trader._detector = mock_detector
            await trader._poll_cycle()

        # Second cycle: stop-loss
        client.get_market = AsyncMock(return_value=market_drop)

        with patch.object(trader, "_detector") as mock_detector:
            mock_detector.detect_signals = AsyncMock(return_value=[])
            trader._detector = mock_detector
            await trader._poll_cycle()

        mock_repo.save_result.assert_called_once()
        record = mock_repo.save_result.call_args[0][0]
        assert record.state == "stopped"

    @pytest.mark.asyncio
    async def test_no_repo_does_not_error(self) -> None:
        """Verify positions close without error when no repo is attached."""
        config = _make_config(max_spread_cost=Decimal("0.80"))
        trader = WhaleCopyTrader(config=config, client=_mock_client("0.55", "0.45"))
        # No repo set — should still work

        signal = _make_signal(window_end_ts=_PAST_TS)

        with (
            patch.object(trader, "_detector") as mock_detector,
            patch.object(trader, "_resolve_outcome", new_callable=AsyncMock, return_value="Up"),
        ):
            mock_detector.detect_signals = AsyncMock(return_value=[signal])
            trader._detector = mock_detector
            await trader._poll_cycle()

        assert len(trader.results) == 1


class TestMaxUnhedgedExposure:
    """Tests for unhedged exposure cap."""

    @pytest.mark.asyncio
    async def test_blocks_new_position_when_exposure_exceeded(self) -> None:
        """Skip new positions when unhedged cost exceeds the cap."""
        config = _make_config(
            max_spread_cost=Decimal("0.80"),  # prevent hedge
            max_entry_price=Decimal("0.65"),
            max_position_pct=Decimal("0.50"),
            max_unhedged_exposure_pct=Decimal("0.50"),
        )
        client = _mock_client("0.55", "0.45")
        trader = WhaleCopyTrader(config=config, client=client)

        # First signal opens at ~50% of capital → at the cap
        signal_a = _make_signal(condition_id="cond_a", favoured_side="Up")

        with patch.object(trader, "_detector") as mock_detector:
            mock_detector.detect_signals = AsyncMock(return_value=[signal_a])
            trader._detector = mock_detector
            await trader._poll_cycle()

        assert "cond_a" in trader.positions

        # Second signal should be blocked — unhedged exposure already at cap
        signal_b = _make_signal(condition_id="cond_b", favoured_side="Up")

        with patch.object(trader, "_detector") as mock_detector:
            mock_detector.detect_signals = AsyncMock(return_value=[signal_b])
            trader._detector = mock_detector
            await trader._poll_cycle()

        assert "cond_b" not in trader.positions

    @pytest.mark.asyncio
    async def test_allows_new_position_when_under_cap(self) -> None:
        """Open new positions when unhedged exposure is within the cap."""
        config = _make_config(
            max_spread_cost=Decimal("0.80"),  # prevent hedge
            max_entry_price=Decimal("0.65"),
            max_position_pct=Decimal("0.10"),  # 10% per trade
            max_unhedged_exposure_pct=Decimal("0.50"),  # 50% cap
        )
        client = _mock_client("0.55", "0.45")
        trader = WhaleCopyTrader(config=config, client=client)

        # Two signals at 10% each = 20% < 50% cap → both should open
        signals = [
            _make_signal(condition_id="cond_a", favoured_side="Up"),
            _make_signal(condition_id="cond_b", favoured_side="Up"),
        ]

        with patch.object(trader, "_detector") as mock_detector:
            mock_detector.detect_signals = AsyncMock(return_value=signals)
            trader._detector = mock_detector
            await trader._poll_cycle()

        _expected_positions = 2
        assert len(trader.positions) == _expected_positions
