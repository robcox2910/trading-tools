"""Tests for the PriceTracker real-time price tracker."""

from decimal import Decimal

from trading_tools.apps.polymarket_bot.price_tracker import PriceTracker

_CONDITION_ID = "cond_tracker_test"
_YES_ASSET = "yes_asset_1"
_NO_ASSET = "no_asset_1"
_YES_PRICE = Decimal("0.65")
_NO_PRICE = Decimal("0.35")


class TestPriceTrackerRegister:
    """Tests for market registration."""

    def test_register_market_creates_mapping(self) -> None:
        """Verify register_market sets up asset-to-condition mappings."""
        tracker = PriceTracker()
        tracker.register_market(_CONDITION_ID, _YES_ASSET, _NO_ASSET)

        result = tracker.get_prices(_CONDITION_ID)

        assert result is not None
        assert result == (None, None)

    def test_register_multiple_markets(self) -> None:
        """Verify multiple markets can be registered independently."""
        tracker = PriceTracker()
        cid_2 = "cond_second"
        tracker.register_market(_CONDITION_ID, _YES_ASSET, _NO_ASSET)
        tracker.register_market(cid_2, "yes_2", "no_2")

        assert tracker.get_prices(_CONDITION_ID) is not None
        assert tracker.get_prices(cid_2) is not None


class TestPriceTrackerUpdate:
    """Tests for price updates from WebSocket events."""

    def test_update_yes_price(self) -> None:
        """Verify updating a YES token price."""
        tracker = PriceTracker()
        tracker.register_market(_CONDITION_ID, _YES_ASSET, _NO_ASSET)

        result = tracker.update(_YES_ASSET, _YES_PRICE)

        assert result == _CONDITION_ID
        prices = tracker.get_prices(_CONDITION_ID)
        assert prices is not None
        assert prices[0] == _YES_PRICE
        assert prices[1] is None

    def test_update_no_price(self) -> None:
        """Verify updating a NO token price."""
        tracker = PriceTracker()
        tracker.register_market(_CONDITION_ID, _YES_ASSET, _NO_ASSET)

        result = tracker.update(_NO_ASSET, _NO_PRICE)

        assert result == _CONDITION_ID
        prices = tracker.get_prices(_CONDITION_ID)
        assert prices is not None
        assert prices[0] is None
        assert prices[1] == _NO_PRICE

    def test_update_both_prices(self) -> None:
        """Verify both YES and NO prices can be updated."""
        tracker = PriceTracker()
        tracker.register_market(_CONDITION_ID, _YES_ASSET, _NO_ASSET)

        tracker.update(_YES_ASSET, _YES_PRICE)
        tracker.update(_NO_ASSET, _NO_PRICE)

        prices = tracker.get_prices(_CONDITION_ID)
        assert prices == (_YES_PRICE, _NO_PRICE)

    def test_update_overwrites_previous(self) -> None:
        """Verify that a newer price overwrites the previous one."""
        tracker = PriceTracker()
        tracker.register_market(_CONDITION_ID, _YES_ASSET, _NO_ASSET)
        tracker.update(_YES_ASSET, Decimal("0.50"))

        tracker.update(_YES_ASSET, _YES_PRICE)

        prices = tracker.get_prices(_CONDITION_ID)
        assert prices is not None
        assert prices[0] == _YES_PRICE

    def test_update_unknown_asset_returns_none(self) -> None:
        """Verify that updating an unregistered asset returns None."""
        tracker = PriceTracker()

        result = tracker.update("unknown_asset", Decimal("0.50"))

        assert result is None


class TestPriceTrackerGetPrices:
    """Tests for price retrieval."""

    def test_get_prices_unregistered_returns_none(self) -> None:
        """Verify get_prices returns None for unregistered condition IDs."""
        tracker = PriceTracker()

        result = tracker.get_prices("nonexistent_cond")

        assert result is None


class TestPriceTrackerClear:
    """Tests for clearing tracker state."""

    def test_clear_removes_all_state(self) -> None:
        """Verify clear() removes all mappings and prices."""
        tracker = PriceTracker()
        tracker.register_market(_CONDITION_ID, _YES_ASSET, _NO_ASSET)
        tracker.update(_YES_ASSET, _YES_PRICE)

        tracker.clear()

        assert tracker.get_prices(_CONDITION_ID) is None
        assert tracker.update(_YES_ASSET, _YES_PRICE) is None

    def test_clear_allows_re_registration(self) -> None:
        """Verify markets can be re-registered after clear()."""
        tracker = PriceTracker()
        tracker.register_market(_CONDITION_ID, _YES_ASSET, _NO_ASSET)
        tracker.update(_YES_ASSET, _YES_PRICE)

        tracker.clear()
        tracker.register_market(_CONDITION_ID, "new_yes", "new_no")
        tracker.update("new_yes", Decimal("0.70"))

        prices = tracker.get_prices(_CONDITION_ID)
        assert prices is not None
        assert prices[0] == Decimal("0.70")
