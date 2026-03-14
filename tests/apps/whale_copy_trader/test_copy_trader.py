"""Tests for the WhaleCopyTrader engine with dual-side spread capture."""

from __future__ import annotations

import time
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from trading_tools.apps.whale_copy_trader.config import WhaleCopyConfig
from trading_tools.apps.whale_copy_trader.copy_trader import (
    WhaleCopyTrader,
    _compute_dual_side_pnl,
    _parse_gamma_prices,
)
from trading_tools.apps.whale_copy_trader.models import (
    CopySignal,
    OpenPosition,
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
_TOPUP_BIAS = Decimal("3.5")
_UP_VOLUME_PCT = Decimal("0.7")
_DOWN_VOLUME_PCT = Decimal("0.3")

_GAMMA_MARKET_DATA = {
    "outcomes": '["Up","Down"]',
    "outcomePrices": '["0.72","0.28"]',
}


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
        "max_bias_scale": Decimal("3.0"),
        "topup_bias_delta": Decimal("0.5"),
        "min_unfavoured_pct": Decimal("0.15"),
    }
    defaults.update(overrides)
    return WhaleCopyConfig(**defaults)  # type: ignore[arg-type]


def _make_signal(
    condition_id: str = "cond_a",
    favoured_side: str = "Up",
    window_end_ts: int = _FUTURE_TS,
    bias_ratio: Decimal = _DEFAULT_BIAS,
    up_volume_pct: Decimal = _UP_VOLUME_PCT,
    down_volume_pct: Decimal = _DOWN_VOLUME_PCT,
) -> CopySignal:
    """Create a CopySignal for testing.

    Args:
        condition_id: Market condition ID.
        favoured_side: Whale's favoured direction.
        window_end_ts: When the market window closes.
        bias_ratio: Whale's bias ratio.
        up_volume_pct: Fraction of whale spend on Up.
        down_volume_pct: Fraction of whale spend on Down.

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
        up_volume_pct=up_volume_pct,
        down_volume_pct=down_volume_pct,
    )


def _mock_gamma() -> AsyncMock:
    """Create a mock GammaClient returning standard market data."""
    gamma = AsyncMock()
    gamma.get_market = AsyncMock(return_value=_GAMMA_MARKET_DATA)
    gamma.close = AsyncMock()
    return gamma


class TestWhaleCopyTrader:
    """Tests for the core dual-side copy-trading engine."""

    @pytest.fixture
    def mock_repo(self) -> AsyncMock:
        """Create a mock WhaleRepository."""
        repo = AsyncMock()
        repo.get_trades = AsyncMock(return_value=[])
        return repo

    @pytest.fixture
    def trader(self, mock_repo: AsyncMock) -> WhaleCopyTrader:
        """Create a WhaleCopyTrader in paper mode."""
        t = WhaleCopyTrader(
            config=_make_config(),
            repo=mock_repo,
        )
        t._gamma = _mock_gamma()
        return t

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
    async def test_paper_opens_dual_side_position(self, trader: WhaleCopyTrader) -> None:
        """Open a paper position with both Up and Down legs."""
        signal = _make_signal()

        with patch.object(trader, "_detector") as mock_detector:
            mock_detector.detect_signals = AsyncMock(return_value=[signal])
            trader._detector = mock_detector
            await trader._poll_cycle()

        assert "cond_a" in trader.positions
        pos = trader.positions["cond_a"]
        assert pos.is_paper
        assert pos.favoured_side == "Up"
        assert pos.up_leg is not None
        assert pos.down_leg is not None
        assert pos.up_leg.entry_price == Decimal("0.72")
        assert pos.down_leg.entry_price == Decimal("0.28")
        assert pos.up_leg.quantity > Decimal(0)
        assert pos.down_leg.quantity > Decimal(0)
        assert pos.total_cost_basis > Decimal(0)

    @pytest.mark.asyncio
    async def test_paper_falls_back_to_midpoint(self, trader: WhaleCopyTrader) -> None:
        """Fall back to 0.50 midpoint when Gamma API fails."""
        signal = _make_signal()
        assert trader._gamma is not None
        trader._gamma.get_market = AsyncMock(side_effect=RuntimeError("API down"))

        with patch.object(trader, "_detector") as mock_detector:
            mock_detector.detect_signals = AsyncMock(return_value=[signal])
            trader._detector = mock_detector
            await trader._poll_cycle()

        pos = trader.positions["cond_a"]
        assert pos.up_leg is not None
        assert pos.up_leg.entry_price == Decimal("0.50")
        assert pos.down_leg is not None
        assert pos.down_leg.entry_price == Decimal("0.50")

    @pytest.mark.asyncio
    async def test_same_signal_no_topup_when_bias_unchanged(self, trader: WhaleCopyTrader) -> None:
        """Do not top up when the same signal appears with unchanged bias."""
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
    async def test_topup_scales_both_legs(self, trader: WhaleCopyTrader) -> None:
        """Top up both legs when whale increases conviction."""
        signal_v1 = _make_signal(bias_ratio=_DEFAULT_BIAS)
        signal_v2 = _make_signal(bias_ratio=_TOPUP_BIAS)

        with patch.object(trader, "_detector") as mock_detector:
            mock_detector.detect_signals = AsyncMock(return_value=[signal_v1])
            trader._detector = mock_detector
            await trader._poll_cycle()

        first_cost = trader.positions["cond_a"].total_cost_basis

        with patch.object(trader, "_detector") as mock_detector:
            mock_detector.detect_signals = AsyncMock(return_value=[signal_v2])
            trader._detector = mock_detector
            await trader._poll_cycle()

        assert trader.positions["cond_a"].total_cost_basis > first_cost
        assert trader.positions["cond_a"].last_bias == _TOPUP_BIAS

    @pytest.mark.asyncio
    async def test_no_topup_when_bias_increase_below_delta(self, trader: WhaleCopyTrader) -> None:
        """Do not top up when bias increase is below topup_bias_delta."""
        signal_v1 = _make_signal(bias_ratio=Decimal("2.5"))
        small_increase = Decimal("2.7")
        signal_v2 = _make_signal(bias_ratio=small_increase)

        with patch.object(trader, "_detector") as mock_detector:
            mock_detector.detect_signals = AsyncMock(return_value=[signal_v1])
            trader._detector = mock_detector
            await trader._poll_cycle()

        first_cost = trader.positions["cond_a"].total_cost_basis

        with patch.object(trader, "_detector") as mock_detector:
            mock_detector.detect_signals = AsyncMock(return_value=[signal_v2])
            trader._detector = mock_detector
            await trader._poll_cycle()

        assert trader.positions["cond_a"].total_cost_basis == first_cost

    @pytest.mark.asyncio
    async def test_flip_closes_both_legs(self, trader: WhaleCopyTrader) -> None:
        """Close both legs and reopen when whale reverses direction."""
        signal_up = _make_signal(favoured_side="Up")
        signal_down = _make_signal(
            favoured_side="Down",
            up_volume_pct=Decimal("0.3"),
            down_volume_pct=Decimal("0.7"),
        )

        with patch.object(trader, "_detector") as mock_detector:
            mock_detector.detect_signals = AsyncMock(return_value=[signal_up])
            trader._detector = mock_detector
            await trader._poll_cycle()

        assert trader.positions["cond_a"].favoured_side == "Up"

        with patch.object(trader, "_detector") as mock_detector:
            mock_detector.detect_signals = AsyncMock(return_value=[signal_down])
            trader._detector = mock_detector
            await trader._poll_cycle()

        assert trader.positions["cond_a"].favoured_side == "Down"
        assert len(trader.results) == 1
        result = trader.results[0]
        assert result.favoured_side == "Up"
        assert result.pnl < Decimal(0)
        # Flip loss should equal negative total cost basis
        assert result.pnl == -result.total_cost_basis

    @pytest.mark.asyncio
    async def test_spread_pnl_when_up_wins(self, trader: WhaleCopyTrader) -> None:
        """Compute correct P&L when Up wins (up_qty - total_cost)."""
        signal = _make_signal(window_end_ts=_PAST_TS)

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
        assert result.up_qty is not None
        expected_pnl = result.up_qty - result.total_cost_basis
        assert result.pnl == expected_pnl

    @pytest.mark.asyncio
    async def test_spread_pnl_when_down_wins(self, trader: WhaleCopyTrader) -> None:
        """Compute correct P&L when Down wins (down_qty - total_cost)."""
        signal = _make_signal(window_end_ts=_PAST_TS)

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
        assert result.down_qty is not None
        expected_pnl = result.down_qty - result.total_cost_basis
        assert result.pnl == expected_pnl

    @pytest.mark.asyncio
    async def test_guaranteed_profit_when_spread_lt_one(self) -> None:
        """Produce positive P&L regardless of winner when spread < $1.00."""
        # Prices sum to 0.95 → guaranteed profit
        gamma_data = {
            "outcomes": '["Up","Down"]',
            "outcomePrices": '["0.47","0.48"]',
        }
        gamma = AsyncMock()
        gamma.get_market = AsyncMock(return_value=gamma_data)
        gamma.close = AsyncMock()

        # 50/50 split so both legs get equal capital
        signal = _make_signal(
            window_end_ts=_PAST_TS,
            up_volume_pct=Decimal("0.5"),
            down_volume_pct=Decimal("0.5"),
        )

        config = _make_config(
            capital=Decimal(1000),
            max_position_pct=Decimal("0.10"),
            min_unfavoured_pct=Decimal("0.0"),
        )
        repo = AsyncMock()
        repo.get_trades = AsyncMock(return_value=[])
        trader = WhaleCopyTrader(config=config, repo=repo)
        trader._gamma = gamma

        # Test both outcomes yield positive PnL
        for winning in ("Up", "Down"):
            trader._positions.clear()
            trader._results.clear()

            with (
                patch.object(trader, "_detector") as mock_detector,
                patch.object(
                    trader, "_resolve_outcome", new_callable=AsyncMock, return_value=winning
                ),
            ):
                mock_detector.detect_signals = AsyncMock(return_value=[signal])
                trader._detector = mock_detector
                await trader._poll_cycle()

            assert len(trader.results) == 1, f"Expected 1 result for {winning}"
            assert trader.results[0].pnl > Decimal(0), (
                f"Expected positive PnL when {winning} wins, got {trader.results[0].pnl}"
            )

    @pytest.mark.asyncio
    async def test_unfavoured_below_minimum_falls_back_to_single(self) -> None:
        """Drop unfavoured leg when its quantity is below 5-token minimum."""
        # Very cheap favoured side + tiny unfavoured allocation → unfavoured < 5 tokens
        gamma_data = {
            "outcomes": '["Up","Down"]',
            "outcomePrices": '["0.10","0.90"]',
        }
        gamma = AsyncMock()
        gamma.get_market = AsyncMock(return_value=gamma_data)
        gamma.close = AsyncMock()

        signal = _make_signal(
            favoured_side="Up",
            up_volume_pct=Decimal("0.95"),
            down_volume_pct=Decimal("0.05"),
        )

        # Small capital to force unfavoured below minimum
        config = _make_config(
            capital=Decimal(30),
            max_position_pct=Decimal("0.10"),
            min_unfavoured_pct=Decimal("0.0"),
        )
        repo = AsyncMock()
        repo.get_trades = AsyncMock(return_value=[])
        trader = WhaleCopyTrader(config=config, repo=repo)
        trader._gamma = gamma

        with patch.object(trader, "_detector") as mock_detector:
            mock_detector.detect_signals = AsyncMock(return_value=[signal])
            trader._detector = mock_detector
            await trader._poll_cycle()

        assert len(trader.positions) == 1
        pos = trader.positions["cond_a"]
        assert pos.up_leg is not None
        assert pos.down_leg is None  # Too small

    @pytest.mark.asyncio
    async def test_min_unfavoured_pct_enforced(self) -> None:
        """Enforce min_unfavoured_pct floor on the unfavoured side."""
        gamma_data = {
            "outcomes": '["Up","Down"]',
            "outcomePrices": '["0.50","0.50"]',
        }
        gamma = AsyncMock()
        gamma.get_market = AsyncMock(return_value=gamma_data)
        gamma.close = AsyncMock()

        # Signal with 95/5 split, but floor is 0.20
        signal = _make_signal(
            favoured_side="Up",
            up_volume_pct=Decimal("0.95"),
            down_volume_pct=Decimal("0.05"),
        )

        config = _make_config(min_unfavoured_pct=Decimal("0.20"))
        repo = AsyncMock()
        repo.get_trades = AsyncMock(return_value=[])
        trader = WhaleCopyTrader(config=config, repo=repo)
        trader._gamma = gamma

        with patch.object(trader, "_detector") as mock_detector:
            mock_detector.detect_signals = AsyncMock(return_value=[signal])
            trader._detector = mock_detector
            await trader._poll_cycle()

        pos = trader.positions["cond_a"]
        assert pos.up_leg is not None
        assert pos.down_leg is not None
        # Down leg should have at least 20% of cost basis
        total = pos.total_cost_basis
        down_pct = pos.down_leg.cost_basis / total
        _expected_floor = Decimal("0.19")  # Allow rounding
        assert down_pct >= _expected_floor

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


class TestComputeDualSidePnl:
    """Tests for the _compute_dual_side_pnl helper."""

    def _make_position(
        self,
        up_qty: Decimal = Decimal(14),
        up_cost: Decimal = Decimal("2.66"),
        down_qty: Decimal = Decimal(5),
        down_cost: Decimal = Decimal("3.95"),
    ) -> OpenPosition:
        """Create an OpenPosition with both legs for P&L testing.

        Args:
            up_qty: Up leg quantity.
            up_cost: Up leg cost basis.
            down_qty: Down leg quantity.
            down_cost: Down leg cost basis.

        Returns:
            An OpenPosition with both legs populated.

        """
        return OpenPosition(
            signal=_make_signal(),
            favoured_side="Up",
            up_leg=SideLeg(
                side="Up",
                entry_price=Decimal("0.19"),
                quantity=up_qty,
                cost_basis=up_cost,
            ),
            down_leg=SideLeg(
                side="Down",
                entry_price=Decimal("0.79"),
                quantity=down_qty,
                cost_basis=down_cost,
            ),
            entry_time=1000,
            last_bias=Decimal("2.0"),
        )

    def test_pnl_when_up_wins(self) -> None:
        """P&L = up_qty - total_cost when Up wins."""
        pos = self._make_position()
        pnl = _compute_dual_side_pnl(pos, "Up")
        # 14 - (2.66 + 3.95) = 14 - 6.61 = 7.39
        assert pnl == Decimal(14) - Decimal("6.61")

    def test_pnl_when_down_wins(self) -> None:
        """P&L = down_qty - total_cost when Down wins."""
        pos = self._make_position()
        pnl = _compute_dual_side_pnl(pos, "Down")
        # 5 - (2.66 + 3.95) = 5 - 6.61 = -1.61
        assert pnl == Decimal(5) - Decimal("6.61")

    def test_pnl_single_leg_only(self) -> None:
        """Handle single-leg position where one leg is None."""
        pos = OpenPosition(
            signal=_make_signal(),
            favoured_side="Up",
            up_leg=SideLeg(
                side="Up",
                entry_price=Decimal("0.72"),
                quantity=Decimal(10),
                cost_basis=Decimal("7.20"),
            ),
            down_leg=None,
            entry_time=1000,
            last_bias=Decimal("2.0"),
        )
        pnl = _compute_dual_side_pnl(pos, "Up")
        assert pnl == Decimal(10) - Decimal("7.20")

        pnl_loss = _compute_dual_side_pnl(pos, "Down")
        assert pnl_loss == Decimal(0) - Decimal("7.20")


class TestParseGammaPrices:
    """Tests for the _parse_gamma_prices helper."""

    def test_parses_both_prices(self) -> None:
        """Return both Up and Down prices from valid Gamma data."""
        market = {
            "outcomes": '["Up","Down"]',
            "outcomePrices": '["0.72","0.28"]',
        }
        prices = _parse_gamma_prices(market)
        assert prices["Up"] == Decimal("0.72")
        assert prices["Down"] == Decimal("0.28")

    def test_derives_missing_down(self) -> None:
        """Derive Down price as 1.0 - Up when Down is missing."""
        market = {
            "outcomes": '["Up"]',
            "outcomePrices": '["0.72"]',
        }
        prices = _parse_gamma_prices(market)
        assert prices["Up"] == Decimal("0.72")
        assert prices["Down"] == Decimal("0.28")

    def test_returns_midpoints_for_invalid_json(self) -> None:
        """Fall back to midpoints when outcome data is invalid JSON."""
        market = {
            "outcomes": "not json",
            "outcomePrices": "not json",
        }
        prices = _parse_gamma_prices(market)
        assert prices["Up"] == Decimal("0.50")
        assert prices["Down"] == Decimal("0.50")

    def test_returns_midpoints_for_missing_fields(self) -> None:
        """Fall back to midpoints when market dict lacks outcome fields."""
        prices = _parse_gamma_prices({})
        assert prices["Up"] == Decimal("0.50")
        assert prices["Down"] == Decimal("0.50")


class TestLiveTradingFlow:
    """Tests for live trading with mocked Polymarket client."""

    @pytest.fixture
    def mock_client(self) -> AsyncMock:
        """Create a mock PolymarketClient."""
        client = AsyncMock()
        client.get_market = AsyncMock(
            return_value=Market(
                condition_id="cond_a",
                question="BTC Up or Down?",
                description="5 min market",
                tokens=(
                    MarketToken(token_id="tok_up", outcome="Up", price=Decimal("0.60")),
                    MarketToken(token_id="tok_down", outcome="Down", price=Decimal("0.40")),
                ),
                end_date="2099-01-01",
                volume=Decimal(10000),
                liquidity=Decimal(5000),
                active=True,
            )
        )
        client.place_order = AsyncMock(
            return_value=OrderResponse(
                order_id="order_123",
                status="matched",
                token_id="tok_up",
                side="BUY",
                price=Decimal("0.60"),
                size=Decimal("16.66"),
                filled=Decimal("16.66"),
            )
        )
        return client

    @pytest.fixture
    def live_trader(self, mock_client: AsyncMock) -> WhaleCopyTrader:
        """Create a WhaleCopyTrader in live mode."""
        repo = AsyncMock()
        repo.get_trades = AsyncMock(return_value=[])
        return WhaleCopyTrader(
            config=_make_config(),
            repo=repo,
            live=True,
            client=mock_client,
        )

    @pytest.mark.asyncio
    async def test_live_places_orders_for_both_sides(
        self, live_trader: WhaleCopyTrader, mock_client: AsyncMock
    ) -> None:
        """Place orders for both Up and Down when in live mode."""
        signal = _make_signal()

        with patch.object(live_trader, "_detector") as mock_detector:
            mock_detector.detect_signals = AsyncMock(return_value=[signal])
            live_trader._detector = mock_detector
            await live_trader._poll_cycle()

        mock_client.get_market.assert_called_once_with("cond_a")
        # Should place 2 orders (one per side)
        _expected_order_count = 2
        assert mock_client.place_order.call_count == _expected_order_count

        pos = live_trader.positions["cond_a"]
        assert not pos.is_paper
        assert pos.up_leg is not None
        assert pos.down_leg is not None
        assert len(pos.all_order_ids) == _expected_order_count

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
