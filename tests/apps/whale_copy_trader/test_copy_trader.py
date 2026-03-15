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
        """Calling stop() causes the run loop to exit."""
        trader.stop()
        assert not trader._running

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
