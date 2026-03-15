"""Tests for MarketScanner spread opportunity discovery."""

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from trading_tools.apps.spread_capture.market_scanner import MarketScanner
from trading_tools.clients.polymarket.models import Market, MarketToken

_UP_PRICE = Decimal("0.48")
_DOWN_PRICE = Decimal("0.47")
_COMBINED = _UP_PRICE + _DOWN_PRICE
_NOW = 1_710_000_100
_WINDOW_START = 1_710_000_000
_WINDOW_END = 1_710_000_300
_MAX_COMBINED = Decimal("0.98")
_MIN_MARGIN = Decimal("0.01")
_EXPECTED_TWO_OPPS = 2


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


def _make_scanner(**overrides: object) -> MarketScanner:
    """Create a MarketScanner with mock client and sensible defaults."""
    client = AsyncMock()
    client.discover_series_markets = AsyncMock(return_value=[("cond_a", "2025-03-10T23:05:00Z")])
    client.get_market = AsyncMock(return_value=_mock_market())
    defaults: dict[str, object] = {
        "client": client,
        "series_slugs": ("btc-updown-5m",),
        "max_combined_cost": _MAX_COMBINED,
        "min_spread_margin": _MIN_MARGIN,
        "max_window_seconds": 0,
        "max_entry_age_pct": Decimal("0.60"),
        "rediscovery_interval": 30,
    }
    defaults.update(overrides)
    return MarketScanner(**defaults)  # type: ignore[arg-type]


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
        assert opps[0].combined == _COMBINED
        assert opps[0].margin == Decimal(1) - _COMBINED

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

        # Second scan within interval — no rediscovery
        with patch("trading_tools.apps.spread_capture.market_scanner.time") as mock_time:
            mock_time.monotonic.return_value = 110.0  # only 10s later
            mock_time.time.return_value = _NOW
            await scanner.scan(set())

        scanner.client.discover_series_markets.assert_not_called()  # type: ignore[attr-defined]

    async def test_sorts_by_margin_descending(self) -> None:
        """Opportunities are sorted by margin, highest first."""
        scanner = _make_scanner()
        market_a = _mock_market(
            condition_id="cond_a", up_price=Decimal("0.48"), down_price=Decimal("0.47")
        )
        market_b = _mock_market(
            condition_id="cond_b", up_price=Decimal("0.45"), down_price=Decimal("0.44")
        )
        scanner.client.discover_series_markets = AsyncMock(  # type: ignore[attr-defined]
            return_value=[
                ("cond_a", "2025-03-10T23:05:00Z"),
                ("cond_b", "2025-03-10T23:05:00Z"),
            ]
        )
        call_count = 0

        async def _get_market(cid: str) -> Market:
            nonlocal call_count
            call_count += 1
            return market_a if cid == "cond_a" else market_b

        scanner.client.get_market = _get_market  # type: ignore[attr-defined]

        with patch("trading_tools.apps.spread_capture.market_scanner.time") as mock_time:
            mock_time.monotonic.return_value = 100.0
            mock_time.time.return_value = _NOW
            opps = await scanner.scan(set())

        assert len(opps) == _EXPECTED_TWO_OPPS
        # cond_b has higher margin (1 - 0.89 = 0.11 > 1 - 0.95 = 0.05)
        assert opps[0].condition_id == "cond_b"
        assert opps[1].condition_id == "cond_a"
