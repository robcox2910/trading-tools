"""Tests for the whale trade / spot price correlator."""

from decimal import Decimal

import pytest

from trading_tools.apps.whale_monitor.analyser import MarketBreakdown
from trading_tools.apps.whale_monitor.correlator import (
    CorrelatedMarket,
    SpotCorrelation,
    compute_correlation,
    correlate_markets,
    format_correlated_analysis,
    parse_asset,
    parse_time_window,
)
from trading_tools.core.models import Candle, Interval

# --- Constants ---

_OPEN_PRICE = Decimal("68000.00")
_CLOSE_PRICE_UP = Decimal("68100.00")
_CLOSE_PRICE_DOWN = Decimal("67900.00")
_HIGH_PRICE = Decimal("68200.00")
_LOW_PRICE = Decimal("67800.00")
_VOLUME = Decimal("10.0")

# March 13, 2026 6:30 PM ET = 2026-03-13T23:30:00 UTC (EDT, UTC-4)
_MAR_13_2026_630PM_ET_UTC = 1741905000
# March 13, 2026 6:45 PM ET = 2026-03-13T23:45:00 UTC
_MAR_13_2026_645PM_ET_UTC = 1741905900
# March 13, 2026 6:00 PM ET = 2026-03-13T23:00:00 UTC
_MAR_13_2026_600PM_ET_UTC = 1741903200
# March 13, 2026 7:00 PM ET = 2026-03-14T00:00:00 UTC
_MAR_13_2026_700PM_ET_UTC = 1741906800

# A reference trade timestamp on March 13, 2026 in ET
_REFERENCE_TS = 1741900000

_BIAS_RATIO_2 = 2.0
_SECONDS_PER_HOUR = 3600


def _make_breakdown(
    title: str = "Bitcoin Up or Down - March 13, 6:30PM-6:45PM ET",
    favoured_side: str = "Up",
    first_trade_ts: int = _REFERENCE_TS,
    bias_ratio: float = _BIAS_RATIO_2,
) -> MarketBreakdown:
    """Create a MarketBreakdown for testing.

    Args:
        title: Market title.
        favoured_side: The whale's favoured side.
        first_trade_ts: Epoch seconds of the first trade.
        bias_ratio: Bias ratio.

    Returns:
        A MarketBreakdown instance.

    """
    return MarketBreakdown(
        condition_id="cond_test",
        title=title,
        slug="test-slug",
        up_volume=100.0,
        down_volume=50.0,
        trade_count=20,
        bias_ratio=bias_ratio,
        favoured_side=favoured_side,
        first_trade_ts=first_trade_ts,
        last_trade_ts=first_trade_ts + 600,
    )


def _make_candle(
    timestamp: int = _MAR_13_2026_630PM_ET_UTC,
    open_price: Decimal = _OPEN_PRICE,
    close_price: Decimal = _CLOSE_PRICE_UP,
    high_price: Decimal = _HIGH_PRICE,
    low_price: Decimal = _LOW_PRICE,
) -> Candle:
    """Create a Candle for testing.

    Args:
        timestamp: Candle timestamp in epoch seconds.
        open_price: Open price.
        close_price: Close price.
        high_price: High price.
        low_price: Low price.

    Returns:
        A Candle instance.

    """
    return Candle(
        symbol="BTC-USD",
        timestamp=timestamp,
        open=open_price,
        high=high_price,
        low=low_price,
        close=close_price,
        volume=_VOLUME,
        interval=Interval.M1,
    )


class TestParseAsset:
    """Tests for parse_asset()."""

    def test_bitcoin_title(self) -> None:
        """Return BTC-USD for Bitcoin titles."""
        assert parse_asset("Bitcoin Up or Down - March 13, 6PM ET") == "BTC-USD"

    def test_ethereum_title(self) -> None:
        """Return ETH-USD for Ethereum titles."""
        assert parse_asset("Ethereum Up or Down - March 13, 6PM ET") == "ETH-USD"

    def test_solana_title(self) -> None:
        """Return SOL-USD for Solana titles."""
        assert parse_asset("Solana Up or Down - March 13, 6PM ET") == "SOL-USD"

    def test_xrp_title(self) -> None:
        """Return XRP-USD for XRP titles."""
        assert parse_asset("XRP Up or Down - March 13, 6PM ET") == "XRP-USD"

    def test_unknown_asset(self) -> None:
        """Return None for unrecognised assets."""
        assert parse_asset("Litecoin Up or Down - March 13, 6PM ET") is None

    def test_case_insensitive(self) -> None:
        """Handle case-insensitive matching."""
        assert parse_asset("bitcoin Up or Down") == "BTC-USD"
        assert parse_asset("BITCOIN Up or Down") == "BTC-USD"


class TestParseTimeWindow:
    """Tests for parse_time_window()."""

    def test_range_pattern(self) -> None:
        """Parse a range pattern like 6:30PM-6:45PM ET."""
        result = parse_time_window(
            "Bitcoin Up or Down - March 13, 6:30PM-6:45PM ET",
            _REFERENCE_TS,
        )
        assert result is not None
        start_ts, end_ts = result
        assert start_ts == _MAR_13_2026_630PM_ET_UTC
        assert end_ts == _MAR_13_2026_645PM_ET_UTC

    def test_single_pattern(self) -> None:
        """Parse a single hourly pattern like 6PM ET."""
        result = parse_time_window(
            "Bitcoin Up or Down - March 13, 6PM ET",
            _REFERENCE_TS,
        )
        assert result is not None
        start_ts, end_ts = result
        assert start_ts == _MAR_13_2026_600PM_ET_UTC
        assert end_ts == _MAR_13_2026_700PM_ET_UTC

    def test_no_time_pattern(self) -> None:
        """Return None when no time pattern is found."""
        result = parse_time_window("Will BTC hit 70k?", _REFERENCE_TS)
        assert result is None

    def test_am_time(self) -> None:
        """Parse AM times correctly."""
        result = parse_time_window(
            "Bitcoin Up or Down - March 13, 9AM ET",
            _REFERENCE_TS,
        )
        assert result is not None
        start_ts, end_ts = result
        # 9AM ET on March 13. End = 10AM ET.
        assert end_ts - start_ts == _SECONDS_PER_HOUR

    def test_date_from_title(self) -> None:
        """Extract the date from the title rather than using fallback."""
        result = parse_time_window(
            "Bitcoin Up or Down - March 13, 6PM ET",
            _REFERENCE_TS,
        )
        assert result is not None
        # Should parse March 13 from the title
        start_ts, _ = result
        assert start_ts == _MAR_13_2026_600PM_ET_UTC


class TestComputeCorrelation:
    """Tests for compute_correlation()."""

    def test_price_up_whale_correct(self) -> None:
        """Whale favours Up and price goes up — whale_correct is True."""
        breakdown = _make_breakdown(favoured_side="Up")
        candles = [
            _make_candle(
                timestamp=_MAR_13_2026_630PM_ET_UTC,
                open_price=_OPEN_PRICE,
                close_price=_OPEN_PRICE,
            ),
            _make_candle(
                timestamp=_MAR_13_2026_630PM_ET_UTC + 60,
                open_price=_OPEN_PRICE,
                close_price=_CLOSE_PRICE_UP,
            ),
        ]
        result = compute_correlation(breakdown, candles)

        assert result.actual_direction == "Up"
        assert result.whale_correct is True
        assert result.price_change_pct > 0

    def test_price_down_whale_incorrect(self) -> None:
        """Whale favours Up but price goes down — whale_correct is False."""
        breakdown = _make_breakdown(favoured_side="Up")
        candles = [
            _make_candle(
                timestamp=_MAR_13_2026_630PM_ET_UTC,
                open_price=_OPEN_PRICE,
                close_price=_OPEN_PRICE,
            ),
            _make_candle(
                timestamp=_MAR_13_2026_630PM_ET_UTC + 60,
                open_price=_OPEN_PRICE,
                close_price=_CLOSE_PRICE_DOWN,
            ),
        ]
        result = compute_correlation(breakdown, candles)

        assert result.actual_direction == "Down"
        assert result.whale_correct is False
        assert result.price_change_pct < 0

    def test_whale_favours_down_price_down(self) -> None:
        """Whale favours Down and price goes down — whale_correct is True."""
        breakdown = _make_breakdown(favoured_side="Down")
        candles = [
            _make_candle(
                timestamp=_MAR_13_2026_630PM_ET_UTC,
                open_price=_OPEN_PRICE,
                close_price=_OPEN_PRICE,
            ),
            _make_candle(
                timestamp=_MAR_13_2026_630PM_ET_UTC + 60,
                open_price=_OPEN_PRICE,
                close_price=_CLOSE_PRICE_DOWN,
            ),
        ]
        result = compute_correlation(breakdown, candles)

        assert result.actual_direction == "Down"
        assert result.whale_correct is True

    def test_flat_price(self) -> None:
        """Return Flat direction when price barely changes."""
        breakdown = _make_breakdown(favoured_side="Up")
        candles = [
            _make_candle(
                timestamp=_MAR_13_2026_630PM_ET_UTC,
                open_price=_OPEN_PRICE,
                close_price=_OPEN_PRICE,
                high_price=_OPEN_PRICE,
                low_price=_OPEN_PRICE,
            ),
        ]
        result = compute_correlation(breakdown, candles)

        assert result.actual_direction == "Flat"
        assert result.whale_correct is False

    def test_volatility_computed(self) -> None:
        """Compute volatility as (high - low) / open * 100."""
        breakdown = _make_breakdown()
        candles = [_make_candle()]
        result = compute_correlation(breakdown, candles)

        expected_vol = (_HIGH_PRICE - _LOW_PRICE) / _OPEN_PRICE * 100
        assert result.volatility_pct == expected_vol

    def test_empty_candles_raises(self) -> None:
        """Raise ValueError for empty candles list."""
        breakdown = _make_breakdown()
        with pytest.raises(ValueError, match="empty candles"):
            compute_correlation(breakdown, [])

    def test_symbol_from_title(self) -> None:
        """Extract symbol from the breakdown title."""
        breakdown = _make_breakdown(title="Ethereum Up or Down - March 13, 6:30PM-6:45PM ET")
        candles = [_make_candle()]
        result = compute_correlation(breakdown, candles)

        assert result.symbol == "ETH-USD"


class TestCorrelateMarkets:
    """Tests for correlate_markets()."""

    @pytest.mark.asyncio
    async def test_parseable_market(self) -> None:
        """Correlate a market with parseable title and available candles."""
        breakdown = _make_breakdown()
        candles = [_make_candle()]

        class MockProvider:
            """Mock candle provider returning predetermined candles."""

            async def get_candles(
                self,
                symbol: str,  # noqa: ARG002
                interval: Interval,  # noqa: ARG002
                start_ts: int,  # noqa: ARG002
                end_ts: int,  # noqa: ARG002
            ) -> list[Candle]:
                """Return preset candles."""
                return candles

        results = await correlate_markets([breakdown], MockProvider())

        assert len(results) == 1
        assert results[0].correlation is not None
        assert results[0].breakdown == breakdown

    @pytest.mark.asyncio
    async def test_unparseable_market(self) -> None:
        """Return None correlation for unparseable market title."""
        breakdown = _make_breakdown(title="Will BTC hit 70k?")

        class MockProvider:
            """Mock candle provider that should not be called."""

            async def get_candles(
                self,
                symbol: str,  # noqa: ARG002
                interval: Interval,  # noqa: ARG002
                start_ts: int,  # noqa: ARG002
                end_ts: int,  # noqa: ARG002
            ) -> list[Candle]:
                """Return empty list (should not be reached)."""
                return []

        results = await correlate_markets([breakdown], MockProvider())

        assert len(results) == 1
        assert results[0].correlation is None

    @pytest.mark.asyncio
    async def test_no_candles_returns_none(self) -> None:
        """Return None correlation when provider returns empty candles."""
        breakdown = _make_breakdown()

        class MockProvider:
            """Mock candle provider returning no candles."""

            async def get_candles(
                self,
                symbol: str,  # noqa: ARG002
                interval: Interval,  # noqa: ARG002
                start_ts: int,  # noqa: ARG002
                end_ts: int,  # noqa: ARG002
            ) -> list[Candle]:
                """Return empty list."""
                return []

        results = await correlate_markets([breakdown], MockProvider())

        assert len(results) == 1
        assert results[0].correlation is None


class TestFormatCorrelatedAnalysis:
    """Tests for format_correlated_analysis()."""

    def test_empty_list(self) -> None:
        """Return a 'no markets' message for empty input."""
        result = format_correlated_analysis([])
        assert "No markets found" in result

    def test_header_present(self) -> None:
        """Include the report header."""
        breakdown = _make_breakdown()
        correlation = SpotCorrelation(
            symbol="BTC-USD",
            window_start_ts=_MAR_13_2026_630PM_ET_UTC,
            window_end_ts=_MAR_13_2026_645PM_ET_UTC,
            open_price=_OPEN_PRICE,
            close_price=_CLOSE_PRICE_UP,
            high_price=_HIGH_PRICE,
            low_price=_LOW_PRICE,
            price_change_pct=Decimal("0.15"),
            volatility_pct=Decimal("0.59"),
            actual_direction="Up",
            whale_correct=True,
        )
        market = CorrelatedMarket(breakdown=breakdown, correlation=correlation)

        result = format_correlated_analysis([market])

        assert "Whale Spot Price Correlation" in result
        assert "Summary" in result

    def test_correct_prediction_counted(self) -> None:
        """Count correct predictions in the summary."""
        breakdown = _make_breakdown()
        correlation = SpotCorrelation(
            symbol="BTC-USD",
            window_start_ts=_MAR_13_2026_630PM_ET_UTC,
            window_end_ts=_MAR_13_2026_645PM_ET_UTC,
            open_price=_OPEN_PRICE,
            close_price=_CLOSE_PRICE_UP,
            high_price=_HIGH_PRICE,
            low_price=_LOW_PRICE,
            price_change_pct=Decimal("0.15"),
            volatility_pct=Decimal("0.59"),
            actual_direction="Up",
            whale_correct=True,
        )
        market = CorrelatedMarket(breakdown=breakdown, correlation=correlation)

        result = format_correlated_analysis([market])

        assert "1 / 1 (100.0%)" in result
        assert "Correct predictions" in result

    def test_skipped_markets_counted(self) -> None:
        """Count skipped (None correlation) markets."""
        breakdown = _make_breakdown(title="Unknown market type")
        market = CorrelatedMarket(breakdown=breakdown, correlation=None)

        result = format_correlated_analysis([market])

        assert "1 skipped" in result

    def test_table_row_shows_dash_for_uncorrelated(self) -> None:
        """Show dashes for uncorrelated markets in the table."""
        breakdown = _make_breakdown(title="Unknown market type")
        market = CorrelatedMarket(breakdown=breakdown, correlation=None)

        result = format_correlated_analysis([market])

        assert "—" in result
