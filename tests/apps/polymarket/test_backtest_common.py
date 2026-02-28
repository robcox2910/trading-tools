"""Tests for shared backtest helpers."""

from decimal import Decimal

import pytest
import typer

from trading_tools.apps.polymarket.backtest_common import (
    build_backtest_result,
    feed_snapshot_to_strategy,
    parse_date,
    resolve_positions,
)
from trading_tools.apps.polymarket_bot.models import MarketSnapshot, PaperTradingResult
from trading_tools.apps.polymarket_bot.portfolio import PaperPortfolio
from trading_tools.apps.polymarket_bot.strategies.late_snipe import PMLateSnipeStrategy
from trading_tools.clients.polymarket.models import OrderBook
from trading_tools.core.models import ZERO, Side

_CONDITION_ID = "cond_test_001"
_THRESHOLD = Decimal("0.80")
_WINDOW_SECONDS = 300
_KELLY_FRAC = Decimal("0.25")
_CAPITAL = Decimal(1000)
_MAX_POS_PCT = Decimal("0.5")
_TIMESTAMP = 1_700_000_000
_FIVE_MINUTES = 300

_EMPTY_BOOK = OrderBook(
    token_id="",
    bids=(),
    asks=(),
    spread=ZERO,
    midpoint=Decimal("0.5"),
)


def _make_snapshot(
    condition_id: str = _CONDITION_ID,
    yes_price: str = "0.50",
    no_price: str = "0.50",
    timestamp: int = _TIMESTAMP,
    end_date: str = "2023-11-14T22:35:00+00:00",
) -> MarketSnapshot:
    """Create a MarketSnapshot for testing.

    Args:
        condition_id: Market condition identifier.
        yes_price: YES token price as string.
        no_price: NO token price as string.
        timestamp: Epoch seconds.
        end_date: ISO-8601 end date.

    Returns:
        A new MarketSnapshot instance.

    """
    return MarketSnapshot(
        condition_id=condition_id,
        question="Test market?",
        timestamp=timestamp,
        yes_price=Decimal(yes_price),
        no_price=Decimal(no_price),
        order_book=_EMPTY_BOOK,
        volume=ZERO,
        liquidity=ZERO,
        end_date=end_date,
    )


class TestParseDate:
    """Tests for the parse_date helper."""

    def test_valid_date(self) -> None:
        """Parse a valid YYYY-MM-DD date to epoch seconds."""
        ts = parse_date("2026-02-20")
        assert ts > 0

    def test_invalid_date_raises(self) -> None:
        """Raise BadParameter for invalid date format."""
        with pytest.raises(typer.BadParameter):
            parse_date("not-a-date")


class TestFeedSnapshotToStrategy:
    """Tests for the feed_snapshot_to_strategy helper."""

    def test_no_signal_returns_none(self) -> None:
        """Return None when strategy does not signal."""
        strategy = PMLateSnipeStrategy(threshold=_THRESHOLD, window_seconds=_WINDOW_SECONDS)
        portfolio = PaperPortfolio(_CAPITAL, _MAX_POS_PCT)
        outcomes: dict[str, str] = {}

        # Price at 0.50 with plenty of time left — no signal
        result = feed_snapshot_to_strategy(
            snapshot=_make_snapshot(
                yes_price="0.50",
                no_price="0.50",
                timestamp=_TIMESTAMP,
                end_date="2023-11-14T23:00:00+00:00",
            ),
            strategy=strategy,
            portfolio=portfolio,
            kelly_frac=_KELLY_FRAC,
            position_outcomes=outcomes,
        )

        assert result is None

    def test_signal_opens_position(self) -> None:
        """Open a position when strategy signals BUY YES."""
        strategy = PMLateSnipeStrategy(threshold=_THRESHOLD, window_seconds=_WINDOW_SECONDS)
        portfolio = PaperPortfolio(_CAPITAL, _MAX_POS_PCT)
        outcomes: dict[str, str] = {}

        # Within snipe window, YES above threshold
        end_date = "2023-11-14T22:17:20+00:00"
        snapshot = _make_snapshot(
            yes_price="0.85",
            no_price="0.15",
            timestamp=_TIMESTAMP,
            end_date=end_date,
        )

        result = feed_snapshot_to_strategy(
            snapshot=snapshot,
            strategy=strategy,
            portfolio=portfolio,
            kelly_frac=_KELLY_FRAC,
            position_outcomes=outcomes,
        )

        assert result is not None
        assert result.side == Side.BUY
        assert _CONDITION_ID in outcomes
        assert outcomes[_CONDITION_ID] == "Yes"

    def test_duplicate_position_skipped(self) -> None:
        """Skip when portfolio already has a position for this market."""
        strategy = PMLateSnipeStrategy(threshold=_THRESHOLD, window_seconds=_WINDOW_SECONDS)
        portfolio = PaperPortfolio(_CAPITAL, _MAX_POS_PCT)
        outcomes: dict[str, str] = {}

        end_date = "2023-11-14T22:17:20+00:00"
        snapshot = _make_snapshot(
            yes_price="0.85",
            no_price="0.15",
            timestamp=_TIMESTAMP,
            end_date=end_date,
        )

        # First call opens position
        feed_snapshot_to_strategy(
            snapshot=snapshot,
            strategy=strategy,
            portfolio=portfolio,
            kelly_frac=_KELLY_FRAC,
            position_outcomes=outcomes,
        )

        # Make a new strategy since the old one remembers the bought condition
        strategy2 = PMLateSnipeStrategy(threshold=_THRESHOLD, window_seconds=_WINDOW_SECONDS)
        result = feed_snapshot_to_strategy(
            snapshot=snapshot,
            strategy=strategy2,
            portfolio=portfolio,
            kelly_frac=_KELLY_FRAC,
            position_outcomes=outcomes,
        )

        assert result is None


class TestResolvePositions:
    """Tests for the resolve_positions helper."""

    def test_resolve_winning_yes_position(self) -> None:
        """Resolve a YES position that won (final price > 0.5)."""
        portfolio = PaperPortfolio(_CAPITAL, _MAX_POS_PCT)
        outcomes: dict[str, str] = {_CONDITION_ID: "Yes"}

        # Open a position first
        portfolio.open_position(
            condition_id=_CONDITION_ID,
            outcome="Yes",
            side=Side.BUY,
            price=Decimal("0.85"),
            quantity=Decimal(10),
            timestamp=_TIMESTAMP,
            reason="test",
            edge=Decimal("0.05"),
        )

        final_prices = {_CONDITION_ID: Decimal("0.90")}
        wins, losses = resolve_positions(
            portfolio=portfolio,
            position_outcomes=outcomes,
            final_prices=final_prices,
            resolve_ts=_TIMESTAMP + _FIVE_MINUTES,
        )

        expected_wins = 1
        assert wins == expected_wins
        assert losses == 0
        assert _CONDITION_ID not in portfolio.positions

    def test_resolve_losing_yes_position(self) -> None:
        """Resolve a YES position that lost (final price <= 0.5)."""
        portfolio = PaperPortfolio(_CAPITAL, _MAX_POS_PCT)
        outcomes: dict[str, str] = {_CONDITION_ID: "Yes"}

        portfolio.open_position(
            condition_id=_CONDITION_ID,
            outcome="Yes",
            side=Side.BUY,
            price=Decimal("0.85"),
            quantity=Decimal(10),
            timestamp=_TIMESTAMP,
            reason="test",
            edge=Decimal("0.05"),
        )

        final_prices = {_CONDITION_ID: Decimal("0.40")}
        wins, losses = resolve_positions(
            portfolio=portfolio,
            position_outcomes=outcomes,
            final_prices=final_prices,
            resolve_ts=_TIMESTAMP + _FIVE_MINUTES,
        )

        assert wins == 0
        expected_losses = 1
        assert losses == expected_losses

    def test_resolve_no_positions_returns_zero(self) -> None:
        """Return zero wins and losses when no positions are open."""
        portfolio = PaperPortfolio(_CAPITAL, _MAX_POS_PCT)

        wins, losses = resolve_positions(
            portfolio=portfolio,
            position_outcomes={},
            final_prices={},
            resolve_ts=_TIMESTAMP + _FIVE_MINUTES,
        )

        assert wins == 0
        assert losses == 0


class TestBuildBacktestResult:
    """Tests for the build_backtest_result helper."""

    def test_builds_result_with_correct_fields(self) -> None:
        """Build result with all expected fields populated."""
        portfolio = PaperPortfolio(_CAPITAL, _MAX_POS_PCT)
        strategy_name = "pm_late_snipe_0.80_300s"
        snapshots_processed = 100
        windows_processed = 10
        wins = 7
        losses = 3

        result = build_backtest_result(
            strategy_name=strategy_name,
            initial_capital=_CAPITAL,
            portfolio=portfolio,
            snapshots_processed=snapshots_processed,
            windows_processed=windows_processed,
            wins=wins,
            losses=losses,
        )

        assert isinstance(result, PaperTradingResult)
        assert result.strategy_name == strategy_name
        assert result.initial_capital == _CAPITAL
        assert result.snapshots_processed == snapshots_processed
        assert result.metrics["windows_processed"] == Decimal(windows_processed)
        assert result.metrics["wins"] == Decimal(wins)
        assert result.metrics["losses"] == Decimal(losses)

    def test_builds_result_with_win_rate(self) -> None:
        """Build result that includes computed win rate."""
        portfolio = PaperPortfolio(_CAPITAL, _MAX_POS_PCT)
        wins = 3
        losses = 1

        result = build_backtest_result(
            strategy_name="test",
            initial_capital=_CAPITAL,
            portfolio=portfolio,
            snapshots_processed=50,
            windows_processed=5,
            wins=wins,
            losses=losses,
        )

        assert "win_rate" in result.metrics
        expected_rate = Decimal(3) / Decimal(4)
        assert result.metrics["win_rate"] == expected_rate

    def test_builds_result_zero_trades(self) -> None:
        """Build result with zero trades omits win_rate."""
        portfolio = PaperPortfolio(_CAPITAL, _MAX_POS_PCT)

        result = build_backtest_result(
            strategy_name="test",
            initial_capital=_CAPITAL,
            portfolio=portfolio,
            snapshots_processed=0,
            windows_processed=0,
            wins=0,
            losses=0,
        )

        assert "win_rate" not in result.metrics
