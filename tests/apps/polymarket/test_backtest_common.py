"""Tests for shared backtest helpers."""

from decimal import Decimal

import pytest
import typer

from trading_tools.apps.polymarket.backtest_common import (
    build_backtest_result,
    compute_order_book_slippage,
    feed_snapshot_to_strategy,
    parse_date,
    resolve_positions,
)
from trading_tools.apps.polymarket_bot.models import MarketSnapshot, PaperTradingResult
from trading_tools.apps.polymarket_bot.portfolio import PaperPortfolio
from trading_tools.apps.polymarket_bot.strategies.late_snipe import PMLateSnipeStrategy
from trading_tools.clients.polymarket.models import OrderBook, OrderLevel
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
    order_book: OrderBook | None = None,
) -> MarketSnapshot:
    """Create a MarketSnapshot for testing.

    Args:
        condition_id: Market condition identifier.
        yes_price: YES token price as string.
        no_price: NO token price as string.
        timestamp: Epoch seconds.
        end_date: ISO-8601 end date.
        order_book: Optional order book. Uses empty book if ``None``.

    Returns:
        A new MarketSnapshot instance.

    """
    return MarketSnapshot(
        condition_id=condition_id,
        question="Test market?",
        timestamp=timestamp,
        yes_price=Decimal(yes_price),
        no_price=Decimal(no_price),
        order_book=order_book if order_book is not None else _EMPTY_BOOK,
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

    def test_check_liquidity_skips_when_insufficient(self) -> None:
        """Skip trade when check_liquidity is True and book is empty."""
        strategy = PMLateSnipeStrategy(threshold=_THRESHOLD, window_seconds=_WINDOW_SECONDS)
        portfolio = PaperPortfolio(_CAPITAL, _MAX_POS_PCT)
        outcomes: dict[str, str] = {}

        # Within snipe window, YES above threshold, but empty order book
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
            check_liquidity=True,
        )

        assert result is None
        assert _CONDITION_ID not in outcomes

    def test_check_liquidity_allows_when_sufficient(self) -> None:
        """Open trade when check_liquidity is True and book has depth."""
        strategy = PMLateSnipeStrategy(threshold=_THRESHOLD, window_seconds=_WINDOW_SECONDS)
        portfolio = PaperPortfolio(_CAPITAL, _MAX_POS_PCT)
        outcomes: dict[str, str] = {}

        book_with_depth = OrderBook(
            token_id="test",
            bids=(),
            asks=(
                OrderLevel(price=Decimal("0.84"), size=Decimal(500)),
                OrderLevel(price=Decimal("0.85"), size=Decimal(500)),
            ),
            spread=ZERO,
            midpoint=Decimal("0.85"),
        )

        end_date = "2023-11-14T22:17:20+00:00"
        snapshot = MarketSnapshot(
            condition_id=_CONDITION_ID,
            question="Test market?",
            timestamp=_TIMESTAMP,
            yes_price=Decimal("0.85"),
            no_price=Decimal("0.15"),
            order_book=book_with_depth,
            volume=ZERO,
            liquidity=ZERO,
            end_date=end_date,
        )

        result = feed_snapshot_to_strategy(
            snapshot=snapshot,
            strategy=strategy,
            portfolio=portfolio,
            kelly_frac=_KELLY_FRAC,
            position_outcomes=outcomes,
            check_liquidity=True,
        )

        assert result is not None
        assert result.side == Side.BUY
        assert _CONDITION_ID in outcomes

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


def _make_order_book(
    bids: tuple[OrderLevel, ...] = (),
    asks: tuple[OrderLevel, ...] = (),
) -> OrderBook:
    """Create an OrderBook for testing.

    Args:
        bids: Bid levels.
        asks: Ask levels.

    Returns:
        A new OrderBook instance.

    """
    return OrderBook(
        token_id="test",
        bids=bids,
        asks=asks,
        spread=ZERO,
        midpoint=Decimal("0.5"),
    )


class TestComputeOrderBookSlippage:
    """Tests for the compute_order_book_slippage helper."""

    def test_empty_book_returns_none(self) -> None:
        """Return None when the order book has no levels."""
        book = _make_order_book()
        result = compute_order_book_slippage(book, Side.BUY, Decimal("0.85"), Decimal(10))
        assert result is None

    def test_single_ask_level_vwap_equals_ask(self) -> None:
        """Return the ask price as VWAP when a single level fills the order."""
        book = _make_order_book(
            asks=(OrderLevel(price=Decimal("0.84"), size=Decimal(100)),),
        )
        result = compute_order_book_slippage(book, Side.BUY, Decimal("0.85"), Decimal(10))
        assert result == Decimal("0.84")

    def test_multiple_ask_levels_vwap(self) -> None:
        """Compute correct VWAP across multiple ask levels."""
        book = _make_order_book(
            asks=(
                OrderLevel(price=Decimal("0.83"), size=Decimal(5)),
                OrderLevel(price=Decimal("0.85"), size=Decimal(10)),
            ),
        )
        # Fill 10: 5 @ 0.83 + 5 @ 0.85 = 4.15 + 4.25 = 8.40 / 10 = 0.84
        result = compute_order_book_slippage(book, Side.BUY, Decimal("0.85"), Decimal(10))
        expected = Decimal("0.84")
        assert result == expected

    def test_insufficient_liquidity_returns_none(self) -> None:
        """Return None when the book cannot fill the full quantity."""
        book = _make_order_book(
            asks=(OrderLevel(price=Decimal("0.84"), size=Decimal(5)),),
        )
        result = compute_order_book_slippage(book, Side.BUY, Decimal("0.85"), Decimal(10))
        assert result is None

    def test_asks_above_price_excluded(self) -> None:
        """Exclude ask levels priced above the snapshot price."""
        book = _make_order_book(
            asks=(
                OrderLevel(price=Decimal("0.84"), size=Decimal(5)),
                OrderLevel(price=Decimal("0.90"), size=Decimal(100)),
            ),
        )
        # Only 5 available at 0.84; 0.90 > 0.85 is excluded → insufficient
        result = compute_order_book_slippage(book, Side.BUY, Decimal("0.85"), Decimal(10))
        assert result is None

    def test_buy_no_walks_bids_with_complement(self) -> None:
        """Walk YES bids for BUY NO, converting to complement prices."""
        # BUY NO at price 0.15: complement = 1 - 0.15 = 0.85
        # Eligible YES bids: price >= 0.85 → bid at 0.86
        # NO fill price = 1 - 0.86 = 0.14
        book = _make_order_book(
            bids=(OrderLevel(price=Decimal("0.86"), size=Decimal(20)),),
        )
        result = compute_order_book_slippage(book, Side.SELL, Decimal("0.15"), Decimal(10))
        expected = Decimal("0.14")
        assert result == expected

    def test_exact_fill_at_single_level(self) -> None:
        """Fill exactly at a single level's available size."""
        exact_qty = Decimal(50)
        book = _make_order_book(
            asks=(OrderLevel(price=Decimal("0.82"), size=exact_qty),),
        )
        result = compute_order_book_slippage(book, Side.BUY, Decimal("0.85"), exact_qty)
        assert result == Decimal("0.82")


# Shared snipe end_date that puts _TIMESTAMP inside the snipe window
_SNIPE_END_DATE = "2023-11-14T22:17:20+00:00"


class TestFeedSnapshotSlippage:
    """Tests for slippage modelling in feed_snapshot_to_strategy."""

    def test_slippage_applied_trade_opens_at_vwap(self) -> None:
        """Open trade at VWAP fill price when slippage modelling is enabled."""
        strategy = PMLateSnipeStrategy(threshold=_THRESHOLD, window_seconds=_WINDOW_SECONDS)
        portfolio = PaperPortfolio(_CAPITAL, _MAX_POS_PCT)
        outcomes: dict[str, str] = {}

        book = _make_order_book(
            asks=(
                OrderLevel(price=Decimal("0.84"), size=Decimal(500)),
                OrderLevel(price=Decimal("0.85"), size=Decimal(500)),
            ),
        )
        snapshot = _make_snapshot(
            yes_price="0.85",
            no_price="0.15",
            end_date=_SNIPE_END_DATE,
            order_book=book,
        )

        result = feed_snapshot_to_strategy(
            snapshot=snapshot,
            strategy=strategy,
            portfolio=portfolio,
            kelly_frac=_KELLY_FRAC,
            position_outcomes=outcomes,
            max_slippage=Decimal("0.05"),
        )

        assert result is not None
        # VWAP should be <= snapshot price (cheaper asks available)
        assert result.price <= Decimal("0.85")
        assert _CONDITION_ID in outcomes

    def test_slippage_exceeds_max_skips_trade(self) -> None:
        """Skip trade when slippage exceeds the maximum tolerance."""
        strategy = PMLateSnipeStrategy(threshold=_THRESHOLD, window_seconds=_WINDOW_SECONDS)
        portfolio = PaperPortfolio(_CAPITAL, _MAX_POS_PCT)
        outcomes: dict[str, str] = {}

        # Only asks at 0.85 → VWAP = 0.85, slippage = 0.0
        # But if we set max_slippage very low (e.g., -0.01) it should skip
        # Actually: asks all at 0.85 when buy_price is 0.85 → slippage = 0.
        # Use asks priced above the snapshot's nominal for realistic slippage.
        # With ask at 0.85, buy_price=0.85, slippage = 0.85 - 0.85 = 0.
        # Use a tiny tolerance to test: set max_slippage = -0.001
        # which means any VWAP >= buy_price is rejected.
        book = _make_order_book(
            asks=(OrderLevel(price=Decimal("0.85"), size=Decimal(500)),),
        )
        snapshot = _make_snapshot(
            yes_price="0.85",
            no_price="0.15",
            end_date=_SNIPE_END_DATE,
            order_book=book,
        )

        # Slippage = 0.85 - 0.85 = 0.0, which is > -0.001 → skip
        result = feed_snapshot_to_strategy(
            snapshot=snapshot,
            strategy=strategy,
            portfolio=portfolio,
            kelly_frac=_KELLY_FRAC,
            position_outcomes=outcomes,
            max_slippage=Decimal("-0.001"),
        )

        assert result is None
        assert _CONDITION_ID not in outcomes

    def test_unfillable_order_skips_trade(self) -> None:
        """Skip trade when the order book cannot fill the quantity."""
        strategy = PMLateSnipeStrategy(threshold=_THRESHOLD, window_seconds=_WINDOW_SECONDS)
        portfolio = PaperPortfolio(_CAPITAL, _MAX_POS_PCT)
        outcomes: dict[str, str] = {}

        # Very thin book — only 1 token available
        book = _make_order_book(
            asks=(OrderLevel(price=Decimal("0.84"), size=Decimal(1)),),
        )
        snapshot = _make_snapshot(
            yes_price="0.85",
            no_price="0.15",
            end_date=_SNIPE_END_DATE,
            order_book=book,
        )

        result = feed_snapshot_to_strategy(
            snapshot=snapshot,
            strategy=strategy,
            portfolio=portfolio,
            kelly_frac=_KELLY_FRAC,
            position_outcomes=outcomes,
            max_slippage=Decimal("0.05"),
        )

        assert result is None
        assert _CONDITION_ID not in outcomes

    def test_max_slippage_none_uses_snapshot_price(self) -> None:
        """Use snapshot price when max_slippage is None (backward compatible)."""
        strategy = PMLateSnipeStrategy(threshold=_THRESHOLD, window_seconds=_WINDOW_SECONDS)
        portfolio = PaperPortfolio(_CAPITAL, _MAX_POS_PCT)
        outcomes: dict[str, str] = {}

        snapshot = _make_snapshot(
            yes_price="0.85",
            no_price="0.15",
            end_date=_SNIPE_END_DATE,
        )

        result = feed_snapshot_to_strategy(
            snapshot=snapshot,
            strategy=strategy,
            portfolio=portfolio,
            kelly_frac=_KELLY_FRAC,
            position_outcomes=outcomes,
        )

        assert result is not None
        assert result.price == Decimal("0.85")
        assert result.slippage == ZERO

    def test_empty_book_with_max_slippage_falls_through(self) -> None:
        """Use snapshot price when book is empty even with max_slippage set."""
        strategy = PMLateSnipeStrategy(threshold=_THRESHOLD, window_seconds=_WINDOW_SECONDS)
        portfolio = PaperPortfolio(_CAPITAL, _MAX_POS_PCT)
        outcomes: dict[str, str] = {}

        snapshot = _make_snapshot(
            yes_price="0.85",
            no_price="0.15",
            end_date=_SNIPE_END_DATE,
        )

        result = feed_snapshot_to_strategy(
            snapshot=snapshot,
            strategy=strategy,
            portfolio=portfolio,
            kelly_frac=_KELLY_FRAC,
            position_outcomes=outcomes,
            max_slippage=Decimal("0.05"),
        )

        assert result is not None
        assert result.price == Decimal("0.85")
        assert result.slippage == ZERO

    def test_slippage_field_recorded_on_trade(self) -> None:
        """Record the slippage amount on the returned PaperTrade."""
        strategy = PMLateSnipeStrategy(threshold=_THRESHOLD, window_seconds=_WINDOW_SECONDS)
        portfolio = PaperPortfolio(_CAPITAL, _MAX_POS_PCT)
        outcomes: dict[str, str] = {}

        # Asks only at 0.85 → VWAP = 0.85, slippage = 0.0
        book = _make_order_book(
            asks=(OrderLevel(price=Decimal("0.85"), size=Decimal(500)),),
        )
        snapshot = _make_snapshot(
            yes_price="0.85",
            no_price="0.15",
            end_date=_SNIPE_END_DATE,
            order_book=book,
        )

        result = feed_snapshot_to_strategy(
            snapshot=snapshot,
            strategy=strategy,
            portfolio=portfolio,
            kelly_frac=_KELLY_FRAC,
            position_outcomes=outcomes,
            max_slippage=Decimal("0.05"),
        )

        assert result is not None
        assert result.slippage == ZERO
