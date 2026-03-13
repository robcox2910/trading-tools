"""Tests for per-market whale trade analysis."""

from trading_tools.apps.whale_monitor.analyser import (
    MarketBreakdown,
    analyse_markets,
    format_market_analysis,
)
from trading_tools.apps.whale_monitor.models import WhaleTrade

_ADDRESS = "0xa45fe11dd1420fca906ceac2c067844379a42429"
_COLLECTED_AT = 1700000000000
_BASE_TS = 1700000000
_MIN_TRADES_2 = 2


def _make_trade(
    side: str = "BUY",
    size: float = 50.0,
    price: float = 0.72,
    condition_id: str = "cond_a",
    title: str = "BTC Up/Down",
    slug: str = "btc-up-down",
    outcome: str = "Up",
    timestamp: int = _BASE_TS,
    tx_hash: str = "tx_001",
) -> WhaleTrade:
    """Create a WhaleTrade instance for market analysis testing.

    Args:
        side: Trade direction.
        size: Token quantity.
        price: Execution price.
        condition_id: Market condition ID.
        title: Market title.
        slug: Market URL slug.
        outcome: Outcome label.
        timestamp: Epoch seconds.
        tx_hash: Transaction hash.

    Returns:
        A WhaleTrade instance.

    """
    return WhaleTrade(
        whale_address=_ADDRESS,
        transaction_hash=tx_hash,
        side=side,
        asset_id="asset_test",
        condition_id=condition_id,
        size=size,
        price=price,
        timestamp=timestamp,
        title=title,
        slug=slug,
        outcome=outcome,
        outcome_index=0,
        collected_at=_COLLECTED_AT,
    )


class TestAnalyseMarkets:
    """Tests for the analyse_markets function."""

    def test_empty_trades(self) -> None:
        """Return empty list for no trades."""
        result = analyse_markets([], min_trades=1)

        assert result == []

    def test_single_market_up_only(self) -> None:
        """Compute breakdown for a market with only Up trades."""
        trades = [
            _make_trade(outcome="Up", size=100.0, price=0.60, tx_hash="tx_1"),
            _make_trade(outcome="Up", size=50.0, price=0.70, tx_hash="tx_2"),
        ]
        result = analyse_markets(trades, min_trades=_MIN_TRADES_2)

        assert len(result) == 1
        m = result[0]
        assert m.condition_id == "cond_a"
        expected_up_volume = 100.0 * 0.60 + 50.0 * 0.70
        assert m.up_volume == expected_up_volume
        assert m.down_volume == 0.0
        assert m.up_size == 150.0  # noqa: PLR2004
        assert m.down_size == 0.0
        assert m.trade_count == _MIN_TRADES_2
        assert m.favoured_side == "Up"

    def test_bias_ratio_calculation(self) -> None:
        """Calculate bias ratio as larger / smaller volume."""
        trades = [
            _make_trade(outcome="Up", size=100.0, price=0.50, tx_hash="tx_1"),
            _make_trade(outcome="Down", size=50.0, price=0.50, tx_hash="tx_2"),
        ]
        result = analyse_markets(trades, min_trades=_MIN_TRADES_2)

        m = result[0]
        expected_ratio = 2.0
        assert m.bias_ratio == expected_ratio
        assert m.favoured_side == "Up"

    def test_bias_ratio_down_favoured(self) -> None:
        """Favour Down when Down volume exceeds Up volume."""
        trades = [
            _make_trade(outcome="Up", size=20.0, price=0.50, tx_hash="tx_1"),
            _make_trade(outcome="Down", size=60.0, price=0.50, tx_hash="tx_2"),
        ]
        result = analyse_markets(trades, min_trades=_MIN_TRADES_2)

        m = result[0]
        expected_ratio = 3.0
        assert m.bias_ratio == expected_ratio
        assert m.favoured_side == "Down"

    def test_bias_ratio_one_side_zero(self) -> None:
        """Use raw volume as bias when one side has zero volume."""
        trades = [
            _make_trade(outcome="Up", size=100.0, price=0.50, tx_hash="tx_1"),
            _make_trade(outcome="Up", size=100.0, price=0.50, tx_hash="tx_2"),
        ]
        result = analyse_markets(trades, min_trades=_MIN_TRADES_2)

        m = result[0]
        expected_volume = 100.0
        assert m.bias_ratio == expected_volume

    def test_min_trades_filter(self) -> None:
        """Filter out markets below the min_trades threshold."""
        trades = [
            _make_trade(condition_id="cond_a", tx_hash="tx_1"),
            _make_trade(condition_id="cond_b", tx_hash="tx_2"),
            _make_trade(condition_id="cond_b", tx_hash="tx_3"),
        ]
        min_trades_2 = 2
        result = analyse_markets(trades, min_trades=min_trades_2)

        assert len(result) == 1
        assert result[0].condition_id == "cond_b"

    def test_sorted_by_last_trade_desc(self) -> None:
        """Sort markets by last_trade_ts descending."""
        ts_early = 1700000000
        ts_late = 1700001000
        trades = [
            _make_trade(condition_id="cond_a", timestamp=ts_early, tx_hash="tx_1"),
            _make_trade(condition_id="cond_a", timestamp=ts_early, tx_hash="tx_2"),
            _make_trade(condition_id="cond_b", timestamp=ts_late, tx_hash="tx_3"),
            _make_trade(condition_id="cond_b", timestamp=ts_late, tx_hash="tx_4"),
        ]
        result = analyse_markets(trades, min_trades=_MIN_TRADES_2)

        assert len(result) == _MIN_TRADES_2
        assert result[0].condition_id == "cond_b"
        assert result[1].condition_id == "cond_a"

    def test_first_and_last_trade_timestamps(self) -> None:
        """Track earliest and latest trade timestamps per market."""
        ts_first = 1700000000
        ts_last = 1700005000
        trades = [
            _make_trade(timestamp=ts_first, tx_hash="tx_1"),
            _make_trade(timestamp=ts_last, tx_hash="tx_2"),
        ]
        result = analyse_markets(trades, min_trades=_MIN_TRADES_2)

        m = result[0]
        assert m.first_trade_ts == ts_first
        assert m.last_trade_ts == ts_last

    def test_multiple_markets(self) -> None:
        """Analyse multiple markets independently."""
        trades = [
            _make_trade(condition_id="cond_a", outcome="Up", size=100.0, tx_hash="tx_1"),
            _make_trade(condition_id="cond_a", outcome="Down", size=50.0, tx_hash="tx_2"),
            _make_trade(
                condition_id="cond_b",
                outcome="Up",
                size=30.0,
                title="ETH Up/Down",
                slug="eth-up-down",
                tx_hash="tx_3",
            ),
            _make_trade(
                condition_id="cond_b",
                outcome="Up",
                size=30.0,
                title="ETH Up/Down",
                slug="eth-up-down",
                tx_hash="tx_4",
            ),
        ]
        result = analyse_markets(trades, min_trades=_MIN_TRADES_2)

        assert len(result) == _MIN_TRADES_2
        titles = {m.title for m in result}
        assert titles == {"BTC Up/Down", "ETH Up/Down"}


class TestFormatMarketAnalysis:
    """Tests for the format_market_analysis function."""

    def test_empty_markets(self) -> None:
        """Return informative message for empty market list."""
        result = format_market_analysis([])

        assert "No markets found" in result

    def test_header_present(self) -> None:
        """Include header and column labels."""
        markets = [
            MarketBreakdown(
                condition_id="cond_a",
                title="BTC Up/Down",
                slug="btc",
                up_volume=52.30,
                down_volume=24.10,
                trade_count=10,
                bias_ratio=2.2,
                favoured_side="Up",
                first_trade_ts=_BASE_TS,
                last_trade_ts=_BASE_TS,
            ),
        ]
        result = format_market_analysis(markets)

        assert "Per-Market Whale Analysis" in result
        assert "Market" in result
        assert "Up $" in result
        assert "Down $" in result
        assert "Bias" in result

    def test_market_row_values(self) -> None:
        """Display volume, bias, and trade count for each market."""
        up_vol = 52.30
        down_vol = 24.10
        markets = [
            MarketBreakdown(
                condition_id="cond_a",
                title="BTC Up/Down",
                slug="btc",
                up_volume=up_vol,
                down_volume=down_vol,
                trade_count=10,
                bias_ratio=2.2,
                favoured_side="Up",
                first_trade_ts=_BASE_TS,
                last_trade_ts=_BASE_TS,
            ),
        ]
        result = format_market_analysis(markets)

        assert "52.30" in result
        assert "24.10" in result
        assert "2.2:1" in result
        assert "Up" in result
        assert "BTC Up/Down" in result

    def test_summary_section(self) -> None:
        """Include summary with total markets, avg bias, and side preference."""
        markets = [
            MarketBreakdown(
                condition_id="cond_a",
                title="Market A",
                slug="a",
                up_volume=100.0,
                down_volume=50.0,
                trade_count=20,
                bias_ratio=2.0,
                favoured_side="Up",
                first_trade_ts=_BASE_TS,
                last_trade_ts=_BASE_TS,
            ),
            MarketBreakdown(
                condition_id="cond_b",
                title="Market B",
                slug="b",
                up_volume=30.0,
                down_volume=90.0,
                trade_count=15,
                bias_ratio=3.0,
                favoured_side="Down",
                first_trade_ts=_BASE_TS,
                last_trade_ts=_BASE_TS,
            ),
        ]
        result = format_market_analysis(markets)

        assert "Total markets: 2" in result
        assert "Average bias ratio: 2.5:1" in result
        assert "Strongest bias: 3.0:1" in result
        assert "Up in 1" in result
        assert "Down in 1" in result

    def test_long_title_truncated(self) -> None:
        """Truncate long market titles with ellipsis."""
        long_title = "A" * 60
        markets = [
            MarketBreakdown(
                condition_id="cond_a",
                title=long_title,
                slug="a",
                trade_count=10,
                bias_ratio=1.5,
                favoured_side="Up",
                first_trade_ts=_BASE_TS,
                last_trade_ts=_BASE_TS,
            ),
        ]
        result = format_market_analysis(markets)

        assert "..." in result
