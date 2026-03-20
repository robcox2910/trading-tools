"""Tests for MarketScanner spread opportunity discovery."""

from decimal import Decimal
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest

from trading_tools.apps.spread_capture.market_scanner import MarketScanner
from trading_tools.clients.polymarket.models import Market, MarketToken, OrderBook, OrderLevel

_UP_PRICE = Decimal("0.48")
_DOWN_PRICE = Decimal("0.47")
_UP_ASK = Decimal("0.48")
_DOWN_ASK = Decimal("0.47")
_COMBINED_ASK = _UP_ASK + _DOWN_ASK
_NOW = 1_710_000_100
_WINDOW_START = 1_710_000_000
_WINDOW_END = 1_710_000_300
_MAX_COMBINED = Decimal("0.98")
_MIN_MARGIN = Decimal("0.01")
_EXPECTED_TWO_OPPS = 2
_ZERO_FEE = Decimal("0.0")
_DEFAULT_FEE_EXPONENT = 2
_ASK_DEPTH = Decimal(100)


def _mock_order_book(
    token_id: str = "tok",
    best_ask: Decimal = Decimal("0.48"),
) -> OrderBook:
    """Build a mock OrderBook with a single best ask level."""
    return OrderBook(
        token_id=token_id,
        bids=(OrderLevel(price=best_ask - Decimal("0.02"), size=Decimal(100)),),
        asks=(OrderLevel(price=best_ask, size=Decimal(100)),),
        spread=Decimal("0.02"),
        midpoint=best_ask - Decimal("0.01"),
    )


def _mock_market(
    condition_id: str = "cond_a",
    up_price: Decimal = _UP_PRICE,
    down_price: Decimal = _DOWN_PRICE,
    question: str = "Bitcoin Up or Down - Mar 10, 6PM ET",
    *,
    active: bool = True,
) -> Market:
    """Build a mock Market with Up/Down tokens."""
    return Market(
        condition_id=condition_id,
        question=question,
        description="",
        tokens=(
            MarketToken(token_id="up_tok", outcome="Up", price=up_price),
            MarketToken(token_id="down_tok", outcome="Down", price=down_price),
        ),
        end_date="2025-03-10T23:05:00Z",
        volume=Decimal(0),
        liquidity=Decimal(0),
        active=active,
    )


def _make_scanner(**overrides: Any) -> MarketScanner:
    """Create a MarketScanner with mock client and sensible defaults."""
    client = AsyncMock()
    client.discover_series_markets = AsyncMock(return_value=[("cond_a", "2025-03-10T23:05:00Z")])
    client.get_market = AsyncMock(return_value=_mock_market())

    async def _get_order_book(token_id: str) -> OrderBook:
        return _mock_order_book(
            token_id=token_id,
            best_ask=_UP_ASK if token_id == "up_tok" else _DOWN_ASK,
        )

    client.get_order_book = AsyncMock(side_effect=_get_order_book)
    defaults: dict[str, Any] = {
        "client": client,
        "series_slugs": ("btc-updown-5m",),
        "max_combined_cost": _MAX_COMBINED,
        "min_spread_margin": _MIN_MARGIN,
        "max_window_seconds": 0,
        "max_entry_age_pct": Decimal("0.60"),
        "rediscovery_interval": 30,
        "fee_rate": _ZERO_FEE,
        "fee_exponent": _DEFAULT_FEE_EXPONENT,
    }
    defaults.update(overrides)
    return MarketScanner(**defaults)


@pytest.mark.asyncio
class TestMarketScanner:
    """Test market scanning and opportunity detection."""

    async def test_discovers_and_scans_markets(self) -> None:
        """Scanner discovers markets and returns opportunities below threshold."""
        scanner = _make_scanner()
        with patch("trading_tools.apps.spread_capture.market_scanner.time") as mock_time:
            mock_time.monotonic.return_value = 100.0
            mock_time.time.return_value = _NOW
            opps = await scanner.scan(set())

        assert len(opps) == 1
        assert opps[0].condition_id == "cond_a"
        assert opps[0].combined == _COMBINED_ASK
        assert opps[0].margin == Decimal(1) - _COMBINED_ASK

    async def test_uses_best_ask_not_midpoint(self) -> None:
        """Scanner uses order book best ask prices, not midpoint prices."""
        scanner = _make_scanner()
        with patch("trading_tools.apps.spread_capture.market_scanner.time") as mock_time:
            mock_time.monotonic.return_value = 100.0
            mock_time.time.return_value = _NOW
            opps = await scanner.scan(set())

        assert len(opps) == 1
        # Prices should be best asks, not midpoints
        assert opps[0].up_price == _UP_ASK
        assert opps[0].down_price == _DOWN_ASK

    async def test_falls_back_to_midpoint_when_no_asks(self) -> None:
        """Scanner falls back to midpoint when order book has no asks."""
        scanner = _make_scanner()
        # Return empty order books (no asks)
        scanner.client.get_order_book = AsyncMock(  # type: ignore[attr-defined]
            return_value=OrderBook(
                token_id="tok",
                bids=(),
                asks=(),
                spread=Decimal(0),
                midpoint=Decimal(0),
            )
        )
        with patch("trading_tools.apps.spread_capture.market_scanner.time") as mock_time:
            mock_time.monotonic.return_value = 100.0
            mock_time.time.return_value = _NOW
            opps = await scanner.scan(set())

        assert len(opps) == 1
        # Falls back to market midpoint prices
        assert opps[0].up_price == _UP_PRICE
        assert opps[0].down_price == _DOWN_PRICE

    async def test_fetches_order_books_concurrently(self) -> None:
        """Scanner fetches order books for both tokens."""
        scanner = _make_scanner()
        with patch("trading_tools.apps.spread_capture.market_scanner.time") as mock_time:
            mock_time.monotonic.return_value = 100.0
            mock_time.time.return_value = _NOW
            await scanner.scan(set())

        # Verify get_order_book was called for both tokens
        raw_calls = scanner.client.get_order_book.call_args_list  # type: ignore[attr-defined]
        calls = cast("list[tuple[tuple[str], dict[str, object]]]", raw_calls)
        token_ids: set[str] = {c[0][0] for c in calls}
        assert token_ids == {"up_tok", "down_tok"}

    async def test_skips_open_positions(self) -> None:
        """Markets with open positions are skipped."""
        scanner = _make_scanner()
        with patch("trading_tools.apps.spread_capture.market_scanner.time") as mock_time:
            mock_time.monotonic.return_value = 100.0
            mock_time.time.return_value = _NOW
            opps = await scanner.scan({"cond_a"})

        assert opps == []

    async def test_skips_above_threshold(self) -> None:
        """Markets where combined >= max_combined_cost are skipped."""
        scanner = _make_scanner()
        scanner.client.get_market = AsyncMock(  # type: ignore[attr-defined]
            return_value=_mock_market(up_price=Decimal("0.50"), down_price=Decimal("0.50"))
        )
        # High ask prices too
        scanner.client.get_order_book = AsyncMock(  # type: ignore[attr-defined]
            return_value=_mock_order_book(best_ask=Decimal("0.50"))
        )
        with patch("trading_tools.apps.spread_capture.market_scanner.time") as mock_time:
            mock_time.monotonic.return_value = 100.0
            mock_time.time.return_value = _NOW
            opps = await scanner.scan(set())

        assert opps == []

    async def test_skips_below_min_margin(self) -> None:
        """Markets where margin < min_spread_margin are skipped."""
        scanner = _make_scanner(min_spread_margin=Decimal("0.10"))
        with patch("trading_tools.apps.spread_capture.market_scanner.time") as mock_time:
            mock_time.monotonic.return_value = 100.0
            mock_time.time.return_value = _NOW
            opps = await scanner.scan(set())

        assert opps == []

    async def test_skips_inactive_markets(self) -> None:
        """Inactive markets are skipped."""
        scanner = _make_scanner()
        scanner.client.get_market = AsyncMock(  # type: ignore[attr-defined]
            return_value=_mock_market(active=False)
        )
        with patch("trading_tools.apps.spread_capture.market_scanner.time") as mock_time:
            mock_time.monotonic.return_value = 100.0
            mock_time.time.return_value = _NOW
            opps = await scanner.scan(set())

        assert opps == []

    async def test_skips_unparseable_asset(self) -> None:
        """Markets with unparseable asset names are skipped."""
        scanner = _make_scanner()
        scanner.client.get_market = AsyncMock(  # type: ignore[attr-defined]
            return_value=_mock_market(question="Unknown Coin Up or Down - Mar 10, 6PM ET")
        )
        with patch("trading_tools.apps.spread_capture.market_scanner.time") as mock_time:
            mock_time.monotonic.return_value = 100.0
            mock_time.time.return_value = _NOW
            opps = await scanner.scan(set())

        assert opps == []

    async def test_respects_rediscovery_interval(self) -> None:
        """Don't rediscover if interval hasn't elapsed."""
        scanner = _make_scanner()
        # First scan triggers discovery
        with patch("trading_tools.apps.spread_capture.market_scanner.time") as mock_time:
            mock_time.monotonic.return_value = 100.0
            mock_time.time.return_value = _NOW
            await scanner.scan(set())

        # Reset mock call count
        scanner.client.discover_series_markets.reset_mock()  # type: ignore[attr-defined]

        # Second scan within interval -- no rediscovery
        with patch("trading_tools.apps.spread_capture.market_scanner.time") as mock_time:
            mock_time.monotonic.return_value = 110.0  # only 10s later
            mock_time.time.return_value = _NOW
            await scanner.scan(set())

        scanner.client.discover_series_markets.assert_not_called()  # type: ignore[attr-defined]

    async def test_sorts_by_margin_descending(self) -> None:
        """Opportunities are sorted by margin, highest first."""
        scanner = _make_scanner()
        # Use different token IDs per market so order books differ
        market_a = Market(
            condition_id="cond_a",
            question="Bitcoin Up or Down - Mar 10, 6PM ET",
            description="",
            tokens=(
                MarketToken(token_id="up_tok_a", outcome="Up", price=Decimal("0.48")),
                MarketToken(token_id="down_tok_a", outcome="Down", price=Decimal("0.47")),
            ),
            end_date="2025-03-10T23:05:00Z",
            volume=Decimal(0),
            liquidity=Decimal(0),
            active=True,
        )
        market_b = Market(
            condition_id="cond_b",
            question="Bitcoin Up or Down - Mar 10, 6PM ET",
            description="",
            tokens=(
                MarketToken(token_id="up_tok_b", outcome="Up", price=Decimal("0.45")),
                MarketToken(token_id="down_tok_b", outcome="Down", price=Decimal("0.44")),
            ),
            end_date="2025-03-10T23:05:00Z",
            volume=Decimal(0),
            liquidity=Decimal(0),
            active=True,
        )
        scanner.client.discover_series_markets = AsyncMock(  # type: ignore[attr-defined]
            return_value=[
                ("cond_a", "2025-03-10T23:05:00Z"),
                ("cond_b", "2025-03-10T23:05:00Z"),
            ]
        )

        async def _get_market(cid: str) -> Market:
            return market_a if cid == "cond_a" else market_b

        scanner.client.get_market = _get_market  # type: ignore[attr-defined]

        # Market A: higher asks (less margin), Market B: lower asks (more margin)
        ask_map: dict[str, Decimal] = {
            "up_tok_a": Decimal("0.48"),
            "down_tok_a": Decimal("0.47"),
            "up_tok_b": Decimal("0.42"),
            "down_tok_b": Decimal("0.41"),
        }

        async def _get_order_book(token_id: str) -> OrderBook:
            ask = ask_map[token_id]
            return _mock_order_book(token_id=token_id, best_ask=ask)

        scanner.client.get_order_book = _get_order_book  # type: ignore[attr-defined]

        with patch("trading_tools.apps.spread_capture.market_scanner.time") as mock_time:
            mock_time.monotonic.return_value = 100.0
            mock_time.time.return_value = _NOW
            opps = await scanner.scan(set())

        assert len(opps) == _EXPECTED_TWO_OPPS
        # cond_b has higher margin (lower asks: 0.42 + 0.41 = 0.83 vs 0.48 + 0.47 = 0.95)
        assert opps[0].condition_id == "cond_b"
        assert opps[1].condition_id == "cond_a"
        assert opps[0].margin > opps[1].margin

    async def test_ask_depth_populated(self) -> None:
        """Spread opportunity includes total ask depth for both sides."""
        scanner = _make_scanner()
        with patch("trading_tools.apps.spread_capture.market_scanner.time") as mock_time:
            mock_time.monotonic.return_value = 100.0
            mock_time.time.return_value = _NOW
            opps = await scanner.scan(set())

        assert len(opps) == 1
        assert opps[0].up_ask_depth == _ASK_DEPTH
        assert opps[0].down_ask_depth == _ASK_DEPTH


@pytest.mark.asyncio
class TestScanPerSide:
    """Test scan_per_side for accumulating strategy."""

    async def test_returns_markets_with_cheap_side(self) -> None:
        """Return markets where at least one side is below threshold."""
        scanner = _make_scanner()
        with patch("trading_tools.apps.spread_capture.market_scanner.time") as mock_time:
            mock_time.monotonic.return_value = 100.0
            mock_time.time.return_value = _NOW
            opps = await scanner.scan_per_side(set(), Decimal("0.50"))

        assert len(opps) == 1
        assert opps[0].condition_id == "cond_a"

    async def test_skips_when_both_sides_above_threshold(self) -> None:
        """Skip markets where both sides are above threshold."""
        scanner = _make_scanner()
        with patch("trading_tools.apps.spread_capture.market_scanner.time") as mock_time:
            mock_time.monotonic.return_value = 100.0
            mock_time.time.return_value = _NOW
            # Set threshold very low so both sides are above it
            opps = await scanner.scan_per_side(set(), Decimal("0.40"))

        assert opps == []

    async def test_skips_open_positions(self) -> None:
        """Markets with open positions are skipped in per-side scan."""
        scanner = _make_scanner()
        with patch("trading_tools.apps.spread_capture.market_scanner.time") as mock_time:
            mock_time.monotonic.return_value = 100.0
            mock_time.time.return_value = _NOW
            opps = await scanner.scan_per_side({"cond_a"}, Decimal("0.50"))

        assert opps == []

    async def test_no_combined_cost_filter(self) -> None:
        """Per-side scan does not filter by combined cost."""
        scanner = _make_scanner()
        # Override with high combined asks that would fail simultaneous scan
        scanner.client.get_order_book = AsyncMock(  # type: ignore[attr-defined]
            return_value=_mock_order_book(best_ask=Decimal("0.50"))
        )
        with patch("trading_tools.apps.spread_capture.market_scanner.time") as mock_time:
            mock_time.monotonic.return_value = 100.0
            mock_time.time.return_value = _NOW
            # Per-side threshold is 0.52, so 0.50 passes per-side check
            opps = await scanner.scan_per_side(set(), Decimal("0.52"))

        # Combined would be 1.00 (fails simultaneous), but per-side should pass
        assert len(opps) == 1

    async def test_sorted_by_cheapest_side(self) -> None:
        """Results are sorted by lowest min(up_ask, down_ask)."""
        scanner = _make_scanner()
        market_a = _mock_market(condition_id="cond_a")
        market_b = _mock_market(condition_id="cond_b")

        scanner.client.discover_series_markets = AsyncMock(  # type: ignore[attr-defined]
            return_value=[
                ("cond_a", "2025-03-10T23:05:00Z"),
                ("cond_b", "2025-03-10T23:05:00Z"),
            ]
        )

        async def _get_market(cid: str) -> Market:
            return market_a if cid == "cond_a" else market_b

        scanner.client.get_market = _get_market  # type: ignore[attr-defined]

        ask_map: dict[str, Decimal] = {
            "up_tok": Decimal("0.48"),
            "down_tok": Decimal("0.47"),
        }
        call_count = 0

        async def _get_order_book(token_id: str) -> OrderBook:
            nonlocal call_count
            call_count += 1
            # Second market (cond_b) gets cheaper asks
            if call_count > _EXPECTED_TWO_OPPS:
                return _mock_order_book(
                    token_id=token_id,
                    best_ask=ask_map.get(token_id, Decimal("0.48")) - Decimal("0.05"),
                )
            return _mock_order_book(
                token_id=token_id, best_ask=ask_map.get(token_id, Decimal("0.48"))
            )

        scanner.client.get_order_book = _get_order_book  # type: ignore[attr-defined]

        with patch("trading_tools.apps.spread_capture.market_scanner.time") as mock_time:
            mock_time.monotonic.return_value = 100.0
            mock_time.time.return_value = _NOW
            opps = await scanner.scan_per_side(set(), Decimal("0.50"))

        assert len(opps) == _EXPECTED_TWO_OPPS
        # Second market should be first (cheaper)
        assert opps[0].condition_id == "cond_b"


@pytest.mark.asyncio
class TestFeeDeduction:
    """Test that Polymarket fees are deducted from margin."""

    async def test_fee_deduction_reduces_margin(self) -> None:
        """Non-zero fee rate reduces the net margin below gross margin."""
        scanner = _make_scanner(fee_rate=Decimal("0.25"), fee_exponent=2)
        with patch("trading_tools.apps.spread_capture.market_scanner.time") as mock_time:
            mock_time.monotonic.return_value = 100.0
            mock_time.time.return_value = _NOW
            opps = await scanner.scan(set())

        assert len(opps) == 1
        gross_margin = Decimal(1) - _COMBINED_ASK
        # Net margin should be less than gross due to fees
        assert opps[0].margin < gross_margin

    async def test_fee_deduction_rejects_thin_margins(self) -> None:
        """Opportunities with margin below min after fee deduction are rejected."""
        # Set min_spread_margin just below gross margin but above net margin
        gross_margin = Decimal(1) - _COMBINED_ASK  # 0.05
        scanner = _make_scanner(
            fee_rate=Decimal("0.25"),
            fee_exponent=2,
            min_spread_margin=gross_margin - Decimal("0.001"),
        )
        with patch("trading_tools.apps.spread_capture.market_scanner.time") as mock_time:
            mock_time.monotonic.return_value = 100.0
            mock_time.time.return_value = _NOW
            opps = await scanner.scan(set())

        # Fee deduction should push net margin below the threshold
        assert opps == []

    async def test_zero_fee_rate_preserves_gross_margin(self) -> None:
        """Zero fee rate preserves gross margin exactly."""
        scanner = _make_scanner(fee_rate=_ZERO_FEE)
        with patch("trading_tools.apps.spread_capture.market_scanner.time") as mock_time:
            mock_time.monotonic.return_value = 100.0
            mock_time.time.return_value = _NOW
            opps = await scanner.scan(set())

        assert len(opps) == 1
        gross_margin = Decimal(1) - _COMBINED_ASK
        assert opps[0].margin == gross_margin
