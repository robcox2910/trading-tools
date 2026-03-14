"""Tests for the WhaleCopyTrader engine."""

from __future__ import annotations

import time
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from trading_tools.apps.whale_copy_trader.config import WhaleCopyConfig
from trading_tools.apps.whale_copy_trader.copy_trader import (
    WhaleCopyTrader,
    _find_token_for_side,
    _parse_gamma_price,
)
from trading_tools.apps.whale_copy_trader.models import CopySignal, OpenPosition
from trading_tools.clients.polymarket.models import Market, MarketToken, OrderResponse

_ADDRESS = "0xwhale"
_FUTURE_TS = 4_000_000_000
_PAST_TS = 1_000_000_000
_EXPECTED_PAPER_PRICE = Decimal("0.72")
_EXPECTED_LIVE_PRICE = Decimal("0.60")
_EXPECTED_EXIT_PRICE = Decimal("1.0")
_EXPECTED_POLL_COUNT_AFTER_DEDUP = 2
_EXPECTED_MULTI_SIGNAL_COUNT = 2
_DEFAULT_BIAS = Decimal("2.5")
_DEFAULT_MIN_BIAS = Decimal("1.5")
_TOPUP_BIAS = Decimal("3.5")

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


def _mock_gamma() -> AsyncMock:
    """Create a mock GammaClient returning standard market data."""
    gamma = AsyncMock()
    gamma.get_market = AsyncMock(return_value=_GAMMA_MARKET_DATA)
    gamma.close = AsyncMock()
    return gamma


class TestWhaleCopyTrader:
    """Tests for the core copy-trading engine."""

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
    async def test_paper_signal_opens_position(self, trader: WhaleCopyTrader) -> None:
        """Open a paper position when a signal is detected."""
        signal = _make_signal()

        with patch.object(trader, "_detector") as mock_detector:
            mock_detector.detect_signals = AsyncMock(return_value=[signal])
            trader._detector = mock_detector
            await trader._poll_cycle()

        assert "cond_a" in trader.positions
        pos = trader.positions["cond_a"]
        assert pos.is_paper
        assert pos.entry_price == _EXPECTED_PAPER_PRICE
        assert pos.quantity > Decimal(0)

    @pytest.mark.asyncio
    async def test_paper_uses_gamma_price(self, trader: WhaleCopyTrader) -> None:
        """Use real Gamma API price instead of hardcoded midpoint."""
        signal = _make_signal()

        with patch.object(trader, "_detector") as mock_detector:
            mock_detector.detect_signals = AsyncMock(return_value=[signal])
            trader._detector = mock_detector
            await trader._poll_cycle()

        pos = trader.positions["cond_a"]
        assert pos.entry_price == Decimal("0.72")

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
        assert pos.entry_price == Decimal("0.50")

    @pytest.mark.asyncio
    async def test_same_signal_no_topup_when_bias_unchanged(self, trader: WhaleCopyTrader) -> None:
        """Do not top up when the same signal appears with unchanged bias."""
        signal = _make_signal()

        with patch.object(trader, "_detector") as mock_detector:
            mock_detector.detect_signals = AsyncMock(return_value=[signal])
            trader._detector = mock_detector

            await trader._poll_cycle()
            first_qty = trader.positions["cond_a"].quantity

            await trader._poll_cycle()

        assert trader.positions["cond_a"].quantity == first_qty
        assert trader.poll_count == _EXPECTED_POLL_COUNT_AFTER_DEDUP

    @pytest.mark.asyncio
    async def test_topup_when_bias_increases(self, trader: WhaleCopyTrader) -> None:
        """Top up position when whale increases conviction."""
        signal_v1 = _make_signal(bias_ratio=_DEFAULT_BIAS)
        signal_v2 = _make_signal(bias_ratio=_TOPUP_BIAS)

        with patch.object(trader, "_detector") as mock_detector:
            mock_detector.detect_signals = AsyncMock(return_value=[signal_v1])
            trader._detector = mock_detector
            await trader._poll_cycle()

        first_qty = trader.positions["cond_a"].quantity

        with patch.object(trader, "_detector") as mock_detector:
            mock_detector.detect_signals = AsyncMock(return_value=[signal_v2])
            trader._detector = mock_detector
            await trader._poll_cycle()

        assert trader.positions["cond_a"].quantity > first_qty
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

        first_qty = trader.positions["cond_a"].quantity

        with patch.object(trader, "_detector") as mock_detector:
            mock_detector.detect_signals = AsyncMock(return_value=[signal_v2])
            trader._detector = mock_detector
            await trader._poll_cycle()

        assert trader.positions["cond_a"].quantity == first_qty

    @pytest.mark.asyncio
    async def test_flip_when_direction_changes(self, trader: WhaleCopyTrader) -> None:
        """Close and reopen when whale reverses direction."""
        signal_up = _make_signal(favoured_side="Up")
        signal_down = _make_signal(favoured_side="Down")

        with patch.object(trader, "_detector") as mock_detector:
            mock_detector.detect_signals = AsyncMock(return_value=[signal_up])
            trader._detector = mock_detector
            await trader._poll_cycle()

        assert trader.positions["cond_a"].side == "Up"

        with patch.object(trader, "_detector") as mock_detector:
            mock_detector.detect_signals = AsyncMock(return_value=[signal_down])
            trader._detector = mock_detector
            await trader._poll_cycle()

        assert trader.positions["cond_a"].side == "Down"
        assert len(trader.results) == 1
        assert trader.results[0].side == "Up"
        assert trader.results[0].pnl < Decimal(0)

    @pytest.mark.asyncio
    async def test_closes_expired_positions(self, trader: WhaleCopyTrader) -> None:
        """Close positions when the market window expires."""
        signal = _make_signal(window_end_ts=_PAST_TS)

        with (
            patch.object(trader, "_detector") as mock_detector,
            patch.object(
                trader, "_resolve_outcome", new_callable=AsyncMock, return_value=Decimal("1.0")
            ),
        ):
            mock_detector.detect_signals = AsyncMock(return_value=[signal])
            trader._detector = mock_detector
            await trader._poll_cycle()

        assert len(trader.positions) == 0
        assert len(trader.results) == 1
        assert trader.results[0].pnl > Decimal(0)

    @pytest.mark.asyncio
    async def test_paper_pnl_tracking(self, trader: WhaleCopyTrader) -> None:
        """Track P&L correctly for paper trades."""
        signal = _make_signal(window_end_ts=_PAST_TS)

        with (
            patch.object(trader, "_detector") as mock_detector,
            patch.object(
                trader, "_resolve_outcome", new_callable=AsyncMock, return_value=Decimal("1.0")
            ),
        ):
            mock_detector.detect_signals = AsyncMock(return_value=[signal])
            trader._detector = mock_detector
            await trader._poll_cycle()

        result = trader.results[0]
        expected_pnl = (_EXPECTED_EXIT_PRICE - _EXPECTED_PAPER_PRICE) * result.quantity
        assert result.pnl == expected_pnl
        assert result.exit_price == _EXPECTED_EXIT_PRICE

    @pytest.mark.asyncio
    async def test_loss_when_whale_wrong(self, trader: WhaleCopyTrader) -> None:
        """Record a loss when the whale's direction is wrong."""
        signal = _make_signal(window_end_ts=_PAST_TS)

        with (
            patch.object(trader, "_detector") as mock_detector,
            patch.object(
                trader, "_resolve_outcome", new_callable=AsyncMock, return_value=Decimal("0.0")
            ),
        ):
            mock_detector.detect_signals = AsyncMock(return_value=[signal])
            trader._detector = mock_detector
            await trader._poll_cycle()

        result = trader.results[0]
        assert result.exit_price == Decimal("0.0")
        assert result.pnl < Decimal(0)

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

    @pytest.mark.asyncio
    async def test_compute_quantity_with_bias_scaling(self, trader: WhaleCopyTrader) -> None:
        """Compute correct bias-scaled position size.

        capital=100, max_position_pct=0.10 -> base_spend=10
        bias=2.5, min_bias=1.5 -> scale=2.5/1.5=1.6667
        price=0.50 -> quantity = (10 * 1.6667) / 0.50 = 33.33
        """
        _expected_qty = Decimal("33.33")
        qty = trader._compute_quantity(Decimal("0.50"), _DEFAULT_BIAS)
        assert qty == _expected_qty

    @pytest.mark.asyncio
    async def test_compute_quantity_caps_at_max_scale(self, trader: WhaleCopyTrader) -> None:
        """Cap bias scaling at max_bias_scale."""
        _expected_capped_qty = Decimal("60.00")
        qty = trader._compute_quantity(Decimal("0.50"), Decimal("100.0"))
        assert qty == _expected_capped_qty

    @pytest.mark.asyncio
    async def test_compute_quantity_no_bias(self, trader: WhaleCopyTrader) -> None:
        """Use base size when no bias_ratio provided."""
        _expected_base_qty = Decimal("20.00")
        qty = trader._compute_quantity(Decimal("0.50"))
        assert qty == _expected_base_qty

    @pytest.mark.asyncio
    async def test_compute_quantity_zero_price(self, trader: WhaleCopyTrader) -> None:
        """Return zero quantity for zero or negative price."""
        assert trader._compute_quantity(Decimal(0)) == Decimal(0)
        assert trader._compute_quantity(Decimal(-1)) == Decimal(0)


class TestOpenPosition:
    """Tests for the OpenPosition model."""

    def test_add_fill_updates_weighted_average(self) -> None:
        """Update entry price to weighted average after top-up."""
        pos = OpenPosition(
            signal=_make_signal(),
            side="Up",
            entry_price=Decimal("0.50"),
            quantity=Decimal("20.00"),
            cost_basis=Decimal("10.00"),
            entry_time=1000,
            last_bias=Decimal("2.0"),
        )
        pos.add_fill(Decimal("0.70"), Decimal("10.00"))

        _expected_total_qty = Decimal("30.00")
        _expected_cost = Decimal("17.00")
        assert pos.quantity == _expected_total_qty
        assert pos.cost_basis == _expected_cost
        assert pos.entry_price == Decimal("0.5667")


class TestParseGammaPrice:
    """Tests for the _parse_gamma_price helper."""

    def test_parses_up_price(self) -> None:
        """Return the Up token price from valid Gamma data."""
        market = {
            "outcomes": '["Up","Down"]',
            "outcomePrices": '["0.72","0.28"]',
        }
        assert _parse_gamma_price(market, "Up") == Decimal("0.72")

    def test_parses_down_price(self) -> None:
        """Return the Down token price from valid Gamma data."""
        market = {
            "outcomes": '["Up","Down"]',
            "outcomePrices": '["0.72","0.28"]',
        }
        assert _parse_gamma_price(market, "Down") == Decimal("0.28")

    def test_returns_midpoint_for_missing_side(self) -> None:
        """Fall back to midpoint when the favoured side is not in outcomes."""
        market = {
            "outcomes": '["Yes","No"]',
            "outcomePrices": '["0.60","0.40"]',
        }
        assert _parse_gamma_price(market, "Up") == Decimal("0.50")

    def test_returns_midpoint_for_invalid_json(self) -> None:
        """Fall back to midpoint when outcome data is invalid JSON."""
        market = {
            "outcomes": "not json",
            "outcomePrices": "not json",
        }
        assert _parse_gamma_price(market, "Up") == Decimal("0.50")

    def test_returns_midpoint_for_missing_fields(self) -> None:
        """Fall back to midpoint when market dict lacks outcome fields."""
        assert _parse_gamma_price({}, "Up") == Decimal("0.50")

    def test_returns_midpoint_for_non_string_fields(self) -> None:
        """Fall back to midpoint when outcome fields are not strings."""
        market = {
            "outcomes": ["Up", "Down"],
            "outcomePrices": [0.72, 0.28],
        }
        assert _parse_gamma_price(market, "Up") == Decimal("0.50")


class TestFindTokenForSide:
    """Tests for the _find_token_for_side helper."""

    def test_finds_matching_token(self) -> None:
        """Return the token matching the favoured side."""
        up_token = MarketToken(token_id="tok_up", outcome="Up", price=Decimal("0.60"))
        down_token = MarketToken(token_id="tok_down", outcome="Down", price=Decimal("0.40"))
        tokens = (up_token, down_token)

        result = _find_token_for_side(tokens, "Up")
        assert result is up_token

    def test_returns_none_when_not_found(self) -> None:
        """Return None when no token matches the side."""
        token = MarketToken(token_id="tok_yes", outcome="Yes", price=Decimal("0.50"))
        result = _find_token_for_side((token,), "Up")
        assert result is None


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
    async def test_live_places_order(
        self, live_trader: WhaleCopyTrader, mock_client: AsyncMock
    ) -> None:
        """Place a real order when in live mode."""
        signal = _make_signal()

        with patch.object(live_trader, "_detector") as mock_detector:
            mock_detector.detect_signals = AsyncMock(return_value=[signal])
            live_trader._detector = mock_detector
            await live_trader._poll_cycle()

        mock_client.get_market.assert_called_once_with("cond_a")
        mock_client.place_order.assert_called_once()

        pos = live_trader.positions["cond_a"]
        assert not pos.is_paper
        assert pos.order_ids == ["order_123"]
        assert pos.entry_price == _EXPECTED_LIVE_PRICE

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
