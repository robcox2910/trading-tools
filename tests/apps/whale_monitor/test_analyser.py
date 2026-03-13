"""Tests for the whale trade analyser."""

from trading_tools.apps.whale_monitor.analyser import (
    WhaleAnalysis,
    analyse_trades,
    format_analysis,
)
from trading_tools.apps.whale_monitor.models import WhaleTrade

_ADDRESS = "0xa45fe11dd1420fca906ceac2c067844379a42429"
_COLLECTED_AT = 1700000000000
_BASE_TS = 1700000000
_EXPECTED_VOLUME_36 = 36.0
_EXPECTED_AVG_SIZE = 50.0
_EXPECTED_AVG_PRICE = 0.72
_TOP_N_2 = 2
_TOTAL_TRADES_3 = 3
_UNIQUE_MARKETS_2 = 2


def _make_trade(
    side: str = "BUY",
    size: float = 50.0,
    price: float = 0.72,
    condition_id: str = "cond_a",
    title: str = "BTC Up/Down",
    outcome: str = "Up",
    timestamp: int = _BASE_TS,
    tx_hash: str = "tx_001",
) -> WhaleTrade:
    """Create a WhaleTrade instance for analysis testing.

    Args:
        side: Trade direction.
        size: Token quantity.
        price: Execution price.
        condition_id: Market condition ID.
        title: Market title.
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
        slug="test",
        outcome=outcome,
        outcome_index=0,
        collected_at=_COLLECTED_AT,
    )


class TestAnalyseTrades:
    """Tests for the analyse_trades function."""

    def test_empty_trades(self) -> None:
        """Return zero-valued analysis for empty trade list."""
        analysis = analyse_trades(_ADDRESS, [])

        assert analysis.total_trades == 0
        assert analysis.total_volume == 0.0

    def test_single_buy_trade(self) -> None:
        """Analyse a single BUY trade."""
        trades = [_make_trade()]
        analysis = analyse_trades(_ADDRESS, trades)

        assert analysis.total_trades == 1
        assert analysis.buy_count == 1
        assert analysis.sell_count == 0
        assert analysis.total_volume == _EXPECTED_VOLUME_36
        assert analysis.avg_size == _EXPECTED_AVG_SIZE
        assert analysis.avg_price == _EXPECTED_AVG_PRICE

    def test_mixed_sides(self) -> None:
        """Count BUY and SELL trades separately."""
        trades = [
            _make_trade(side="BUY", tx_hash="tx_1"),
            _make_trade(side="SELL", tx_hash="tx_2"),
            _make_trade(side="BUY", tx_hash="tx_3"),
        ]
        analysis = analyse_trades(_ADDRESS, trades)

        assert analysis.buy_count == 2  # noqa: PLR2004
        assert analysis.sell_count == 1

    def test_unique_markets(self) -> None:
        """Count distinct condition IDs as unique markets."""
        trades = [
            _make_trade(condition_id="cond_a", tx_hash="tx_1"),
            _make_trade(condition_id="cond_a", tx_hash="tx_2"),
            _make_trade(condition_id="cond_b", tx_hash="tx_3"),
        ]
        analysis = analyse_trades(_ADDRESS, trades)

        assert analysis.unique_markets == _UNIQUE_MARKETS_2

    def test_outcome_breakdown(self) -> None:
        """Track outcome distribution."""
        trades = [
            _make_trade(outcome="Up", tx_hash="tx_1"),
            _make_trade(outcome="Up", tx_hash="tx_2"),
            _make_trade(outcome="Down", tx_hash="tx_3"),
        ]
        analysis = analyse_trades(_ADDRESS, trades)

        assert analysis.outcome_breakdown["Up"] == 2  # noqa: PLR2004
        assert analysis.outcome_breakdown["Down"] == 1

    def test_top_markets(self) -> None:
        """Return top N markets by trade count."""
        trades = [
            _make_trade(title="Market A", tx_hash="tx_1"),
            _make_trade(title="Market A", tx_hash="tx_2"),
            _make_trade(title="Market B", tx_hash="tx_3"),
        ]
        analysis = analyse_trades(_ADDRESS, trades, top_n=_TOP_N_2)

        assert len(analysis.top_markets) == _TOP_N_2
        assert analysis.top_markets[0] == ("Market A", 2)

    def test_hourly_distribution(self) -> None:
        """Track trades by hour of day."""
        # 1700000000 is 2023-11-14 22:13:20 UTC → hour 22
        trades = [_make_trade(timestamp=_BASE_TS)]
        analysis = analyse_trades(_ADDRESS, trades)

        hour_22 = 22
        assert analysis.hourly_distribution.get(hour_22, 0) > 0

    def test_market_breakdown(self) -> None:
        """Track per-market trade counts."""
        trades = [
            _make_trade(title="BTC Up/Down", tx_hash="tx_1"),
            _make_trade(title="BTC Up/Down", tx_hash="tx_2"),
            _make_trade(title="ETH Up/Down", tx_hash="tx_3"),
        ]
        analysis = analyse_trades(_ADDRESS, trades)

        assert analysis.market_breakdown["BTC Up/Down"] == 2  # noqa: PLR2004
        assert analysis.market_breakdown["ETH Up/Down"] == 1


class TestFormatAnalysis:
    """Tests for the format_analysis function."""

    def test_format_empty_analysis(self) -> None:
        """Format a zero-valued analysis without error."""
        analysis = WhaleAnalysis(address=_ADDRESS)
        result = format_analysis(analysis)

        assert _ADDRESS in result
        assert "Total trades: 0" in result

    def test_format_with_data(self) -> None:
        """Format analysis with populated data."""
        trades = [
            _make_trade(side="BUY", tx_hash="tx_1"),
            _make_trade(side="SELL", tx_hash="tx_2"),
            _make_trade(side="BUY", outcome="Down", tx_hash="tx_3"),
        ]
        analysis = analyse_trades(_ADDRESS, trades)
        result = format_analysis(analysis)

        assert f"Total trades: {_TOTAL_TRADES_3}" in result
        assert "BUY:" in result
        assert "SELL:" in result
        assert "Side Breakdown:" in result

    def test_format_long_market_title_truncated(self) -> None:
        """Truncate long market titles with ellipsis."""
        long_title = "A" * 60
        trades = [_make_trade(title=long_title)]
        analysis = analyse_trades(_ADDRESS, trades)
        result = format_analysis(analysis)

        assert "..." in result
