"""Tests for PaperPortfolio multi-position tracker."""

from decimal import Decimal

import pytest

from trading_tools.apps.polymarket_bot.models import PaperTrade
from trading_tools.apps.polymarket_bot.portfolio import PaperPortfolio
from trading_tools.core.models import ZERO, Side

_INITIAL_CAPITAL = Decimal(1000)
_MAX_POSITION_PCT = Decimal("0.1")
_CONDITION_A = "cond_a"
_CONDITION_B = "cond_b"
_TIMESTAMP = 1700000000


@pytest.fixture
def portfolio() -> PaperPortfolio:
    """Create a portfolio with standard test parameters."""
    return PaperPortfolio(_INITIAL_CAPITAL, _MAX_POSITION_PCT)


class TestOpenPosition:
    """Tests for opening positions."""

    def test_open_position_deducts_cash(self, portfolio: PaperPortfolio) -> None:
        """Test that opening a position deducts cost from cash."""
        trade = portfolio.open_position(
            condition_id=_CONDITION_A,
            outcome="Yes",
            side=Side.BUY,
            price=Decimal("0.50"),
            quantity=Decimal(100),
            timestamp=_TIMESTAMP,
            reason="test buy",
            edge=Decimal("0.05"),
        )
        assert trade is not None
        assert isinstance(trade, PaperTrade)
        # 1000 - (0.50 * 100) = 950
        assert portfolio.capital == Decimal(950)

    def test_open_position_records_trade(self, portfolio: PaperPortfolio) -> None:
        """Test that opening a position records a PaperTrade."""
        portfolio.open_position(
            condition_id=_CONDITION_A,
            outcome="Yes",
            side=Side.BUY,
            price=Decimal("0.50"),
            quantity=Decimal(100),
            timestamp=_TIMESTAMP,
            reason="test",
            edge=Decimal("0.05"),
        )
        assert len(portfolio.trades) == 1
        assert portfolio.trades[0].side == Side.BUY

    def test_open_duplicate_position_rejected(self, portfolio: PaperPortfolio) -> None:
        """Test that opening a second position for the same market is rejected."""
        portfolio.open_position(
            condition_id=_CONDITION_A,
            outcome="Yes",
            side=Side.BUY,
            price=Decimal("0.50"),
            quantity=Decimal(10),
            timestamp=_TIMESTAMP,
            reason="first",
            edge=Decimal("0.05"),
        )
        result = portfolio.open_position(
            condition_id=_CONDITION_A,
            outcome="Yes",
            side=Side.BUY,
            price=Decimal("0.50"),
            quantity=Decimal(10),
            timestamp=_TIMESTAMP + 1,
            reason="duplicate",
            edge=Decimal("0.05"),
        )
        assert result is None

    def test_open_exceeds_allocation_rejected(self, portfolio: PaperPortfolio) -> None:
        """Test that a position exceeding max allocation is rejected."""
        # max allocation = 1000 * 0.1 = 100; cost = 0.50 * 300 = 150
        result = portfolio.open_position(
            condition_id=_CONDITION_A,
            outcome="Yes",
            side=Side.BUY,
            price=Decimal("0.50"),
            quantity=Decimal(300),
            timestamp=_TIMESTAMP,
            reason="too big",
            edge=Decimal("0.05"),
        )
        assert result is None

    def test_multiple_positions_different_markets(self, portfolio: PaperPortfolio) -> None:
        """Test opening positions in multiple markets simultaneously."""
        portfolio.open_position(
            condition_id=_CONDITION_A,
            outcome="Yes",
            side=Side.BUY,
            price=Decimal("0.50"),
            quantity=Decimal(50),
            timestamp=_TIMESTAMP,
            reason="market A",
            edge=Decimal("0.05"),
        )
        portfolio.open_position(
            condition_id=_CONDITION_B,
            outcome="No",
            side=Side.BUY,
            price=Decimal("0.30"),
            quantity=Decimal(50),
            timestamp=_TIMESTAMP,
            reason="market B",
            edge=Decimal("0.03"),
        )
        assert len(portfolio.positions) == 2
        # 1000 - 25 - 15 = 960
        assert portfolio.capital == Decimal(960)


class TestClosePosition:
    """Tests for closing positions."""

    def test_close_position_credits_cash(self, portfolio: PaperPortfolio) -> None:
        """Test that closing a profitable position credits cash correctly."""
        portfolio.open_position(
            condition_id=_CONDITION_A,
            outcome="Yes",
            side=Side.BUY,
            price=Decimal("0.50"),
            quantity=Decimal(100),
            timestamp=_TIMESTAMP,
            reason="buy",
            edge=Decimal("0.05"),
        )
        trade = portfolio.close_position(_CONDITION_A, Decimal("0.70"), _TIMESTAMP + 100)
        assert trade is not None
        assert trade.side == Side.SELL
        expected_capital = Decimal(1020)
        assert portfolio.capital == expected_capital

    def test_close_losing_position(self, portfolio: PaperPortfolio) -> None:
        """Test that closing a losing position deducts from cash correctly."""
        portfolio.open_position(
            condition_id=_CONDITION_A,
            outcome="Yes",
            side=Side.BUY,
            price=Decimal("0.50"),
            quantity=Decimal(100),
            timestamp=_TIMESTAMP,
            reason="buy",
            edge=Decimal("0.05"),
        )
        portfolio.close_position(_CONDITION_A, Decimal("0.30"), _TIMESTAMP + 100)
        expected_capital = Decimal(980)
        assert portfolio.capital == expected_capital

    def test_close_nonexistent_position_returns_none(self, portfolio: PaperPortfolio) -> None:
        """Test that closing a nonexistent position returns None."""
        result = portfolio.close_position("nonexistent", Decimal("0.5"), _TIMESTAMP)
        assert result is None

    def test_close_removes_position(self, portfolio: PaperPortfolio) -> None:
        """Test that closing removes the position from tracking."""
        portfolio.open_position(
            condition_id=_CONDITION_A,
            outcome="Yes",
            side=Side.BUY,
            price=Decimal("0.50"),
            quantity=Decimal(10),
            timestamp=_TIMESTAMP,
            reason="buy",
            edge=Decimal("0.05"),
        )
        portfolio.close_position(_CONDITION_A, Decimal("0.60"), _TIMESTAMP + 100)
        assert _CONDITION_A not in portfolio.positions


_FEE_RATE = Decimal("0.25")
_FEE_EXPONENT = 2


class TestFeeDeduction:
    """Tests for polynomial fee deduction on opens and closes.

    Fee formula: C * p * feeRate * (p * (1 - p))^exponent
    With rate=0.25 and exponent=2 (crypto defaults).
    """

    @pytest.fixture
    def fee_portfolio(self) -> PaperPortfolio:
        """Create a portfolio with crypto polynomial fee parameters."""
        return PaperPortfolio(_INITIAL_CAPITAL, _MAX_POSITION_PCT, _FEE_RATE, _FEE_EXPONENT)

    def test_open_position_deducts_fee(self, fee_portfolio: PaperPortfolio) -> None:
        """Test that opening a position deducts cost plus polynomial fee from cash."""
        trade = fee_portfolio.open_position(
            condition_id=_CONDITION_A,
            outcome="Yes",
            side=Side.BUY,
            price=Decimal("0.50"),
            quantity=Decimal(100),
            timestamp=_TIMESTAMP,
            reason="test buy",
            edge=Decimal("0.05"),
        )
        assert trade is not None
        # fee = 100 * 0.50 * 0.25 * (0.50 * 0.50)^2 = 0.78125
        # cost = 50 + 0.78125 = 50.78125
        expected_cash = Decimal("949.218750000000")
        assert fee_portfolio.capital == expected_cash

    def test_close_position_deducts_fee(self, fee_portfolio: PaperPortfolio) -> None:
        """Test that closing a position deducts polynomial fee from gross proceeds."""
        fee_portfolio.open_position(
            condition_id=_CONDITION_A,
            outcome="Yes",
            side=Side.BUY,
            price=Decimal("0.50"),
            quantity=Decimal(100),
            timestamp=_TIMESTAMP,
            reason="buy",
            edge=Decimal("0.05"),
        )
        trade = fee_portfolio.close_position(_CONDITION_A, Decimal("0.70"), _TIMESTAMP + 100)
        assert trade is not None
        # close fee = 100 * 0.70 * 0.25 * (0.70 * 0.30)^2 = 0.77175
        # net = 70 - 0.77175 = 69.22825
        expected_cash = Decimal("949.218750000000") + Decimal("69.228250000000")
        assert fee_portfolio.capital == expected_cash

    def test_fee_recorded_on_open_trade(self, fee_portfolio: PaperPortfolio) -> None:
        """Test that the fee_paid field is set correctly on open trades."""
        trade = fee_portfolio.open_position(
            condition_id=_CONDITION_A,
            outcome="Yes",
            side=Side.BUY,
            price=Decimal("0.50"),
            quantity=Decimal(100),
            timestamp=_TIMESTAMP,
            reason="test",
            edge=Decimal("0.05"),
        )
        assert trade is not None
        expected_fee = Decimal("0.781250000000")
        assert trade.fee_paid == expected_fee

    def test_fee_recorded_on_close_trade(self, fee_portfolio: PaperPortfolio) -> None:
        """Test that the fee_paid field is set correctly on close trades."""
        fee_portfolio.open_position(
            condition_id=_CONDITION_A,
            outcome="Yes",
            side=Side.BUY,
            price=Decimal("0.50"),
            quantity=Decimal(100),
            timestamp=_TIMESTAMP,
            reason="buy",
            edge=Decimal("0.05"),
        )
        trade = fee_portfolio.close_position(_CONDITION_A, Decimal("0.70"), _TIMESTAMP + 100)
        assert trade is not None
        expected_fee = Decimal("0.771750000000")
        assert trade.fee_paid == expected_fee

    def test_max_quantity_accounts_for_fees(self, fee_portfolio: PaperPortfolio) -> None:
        """Test that max_quantity_for includes polynomial fees in effective price."""
        # max allocation = 1000 * 0.1 = 100
        # fee_per_token = 0.50 * 0.25 * (0.25)^2 = 0.0078125
        # effective price = 0.5078125
        # max qty = floor(100 / 0.5078125) = 197
        qty = fee_portfolio.max_quantity_for(Decimal("0.50"))
        expected_qty = Decimal(197)
        assert qty == expected_qty

    def test_total_fees_property(self, fee_portfolio: PaperPortfolio) -> None:
        """Test that total_fees sums all fee_paid values."""
        fee_portfolio.open_position(
            condition_id=_CONDITION_A,
            outcome="Yes",
            side=Side.BUY,
            price=Decimal("0.50"),
            quantity=Decimal(100),
            timestamp=_TIMESTAMP,
            reason="buy",
            edge=Decimal("0.05"),
        )
        fee_portfolio.close_position(_CONDITION_A, Decimal("0.70"), _TIMESTAMP + 100)
        # open fee = 0.78125, close fee = 0.77175
        expected_total = Decimal("1.553000000000")
        assert fee_portfolio.total_fees == expected_total

    def test_zero_fee_rate_unchanged(self) -> None:
        """Test that fee_rate=0 behaves identically to fee-free portfolio."""
        port = PaperPortfolio(_INITIAL_CAPITAL, _MAX_POSITION_PCT, ZERO)
        port.open_position(
            condition_id=_CONDITION_A,
            outcome="Yes",
            side=Side.BUY,
            price=Decimal("0.50"),
            quantity=Decimal(100),
            timestamp=_TIMESTAMP,
            reason="buy",
            edge=Decimal("0.05"),
        )
        # No fee → cash = 1000 - 50 = 950
        assert port.capital == Decimal(950)
        assert port.total_fees == ZERO

    def test_fee_zero_at_extremes(self) -> None:
        """Test that fees approach zero at price extremes (p near 0 or 1)."""
        port = PaperPortfolio(_INITIAL_CAPITAL, Decimal(1), _FEE_RATE, _FEE_EXPONENT)
        # At p=0.99: fee = 100 * 0.99 * 0.25 * (0.99 * 0.01)^2 ~= 0.002426
        trade_high = port.open_position(
            condition_id=_CONDITION_A,
            outcome="Yes",
            side=Side.BUY,
            price=Decimal("0.99"),
            quantity=Decimal(100),
            timestamp=_TIMESTAMP,
            reason="extreme high",
            edge=Decimal("0.01"),
        )
        assert trade_high is not None
        assert trade_high.fee_paid < Decimal("0.01")

    def test_fee_max_at_midpoint(self) -> None:
        """Test that fees are highest at p=0.50 compared to other prices."""
        port_mid = PaperPortfolio(_INITIAL_CAPITAL, Decimal(1), _FEE_RATE, _FEE_EXPONENT)
        port_high = PaperPortfolio(_INITIAL_CAPITAL, Decimal(1), _FEE_RATE, _FEE_EXPONENT)
        qty = Decimal(100)

        trade_mid = port_mid.open_position(
            condition_id=_CONDITION_A,
            outcome="Yes",
            side=Side.BUY,
            price=Decimal("0.50"),
            quantity=qty,
            timestamp=_TIMESTAMP,
            reason="midpoint",
            edge=Decimal("0.05"),
        )
        trade_high = port_high.open_position(
            condition_id=_CONDITION_A,
            outcome="Yes",
            side=Side.BUY,
            price=Decimal("0.80"),
            quantity=qty,
            timestamp=_TIMESTAMP,
            reason="high price",
            edge=Decimal("0.05"),
        )
        assert trade_mid is not None
        assert trade_high is not None
        assert trade_mid.fee_paid > trade_high.fee_paid


class TestMarkToMarket:
    """Tests for mark-to-market valuation."""

    def test_mark_to_market_updates_equity(self, portfolio: PaperPortfolio) -> None:
        """Test that MTM updates reflected in total_equity."""
        portfolio.open_position(
            condition_id=_CONDITION_A,
            outcome="Yes",
            side=Side.BUY,
            price=Decimal("0.50"),
            quantity=Decimal(100),
            timestamp=_TIMESTAMP,
            reason="buy",
            edge=Decimal("0.05"),
        )
        # Initial equity = 950 cash + 50 position value = 1000
        assert portfolio.total_equity == Decimal(1000)

        portfolio.mark_to_market(_CONDITION_A, Decimal("0.60"))
        # equity = 950 cash + unrealised (0.60-0.50)*100 + position cost 50 = 1010
        assert portfolio.total_equity == Decimal(1010)

    def test_mark_to_market_ignores_unknown(self, portfolio: PaperPortfolio) -> None:
        """Test that MTM on unknown condition_id is a no-op."""
        portfolio.mark_to_market("unknown", Decimal("0.5"))
        assert portfolio.total_equity == _INITIAL_CAPITAL


class TestMaxQuantity:
    """Tests for max_quantity_for helper."""

    def test_max_quantity_respects_allocation(self, portfolio: PaperPortfolio) -> None:
        """Test that max quantity respects per-market allocation limit."""
        # max allocation = 1000 * 0.1 = 100; at price 0.50, qty = 200
        qty = portfolio.max_quantity_for(Decimal("0.50"))
        assert qty == Decimal(200)

    def test_max_quantity_zero_price(self, portfolio: PaperPortfolio) -> None:
        """Test that zero price returns zero quantity."""
        assert portfolio.max_quantity_for(ZERO) == ZERO


class TestProperties:
    """Tests for portfolio properties."""

    def test_initial_capital(self, portfolio: PaperPortfolio) -> None:
        """Test that initial capital equals starting cash."""
        assert portfolio.capital == _INITIAL_CAPITAL

    def test_empty_positions(self, portfolio: PaperPortfolio) -> None:
        """Test that empty portfolio has no positions."""
        assert portfolio.positions == {}

    def test_empty_trades(self, portfolio: PaperPortfolio) -> None:
        """Test that empty portfolio has no trades."""
        assert portfolio.trades == []

    def test_total_equity_no_positions(self, portfolio: PaperPortfolio) -> None:
        """Test that total equity equals cash when no positions open."""
        assert portfolio.total_equity == _INITIAL_CAPITAL
