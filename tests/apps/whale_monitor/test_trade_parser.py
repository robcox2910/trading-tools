"""Tests for the shared whale trade parser.

Verify that parse_whale_trade correctly handles field naming variants,
malformed records, and edge cases.
"""

from typing import Any

from trading_tools.apps.whale_monitor.trade_parser import parse_whale_trade

_VALID_TRADE: dict[str, Any] = {
    "transactionHash": "0xabc123",
    "side": "BUY",
    "asset_id": "token_001",
    "conditionId": "cond_001",
    "size": "100.5",
    "price": "0.65",
    "timestamp": 1710000000,
    "market": "Will BTC hit $100k?",
    "slug": "will-btc-hit-100k",
    "outcome": "Yes",
    "outcome_index": 0,
}

_ADDRESS = "0xwhale"
_COLLECTED_AT = 1710000500000


class TestParseWhaleTrade:
    """Test parse_whale_trade with valid and invalid inputs."""

    def test_valid_trade(self) -> None:
        """Parse a well-formed trade dict into a WhaleTrade."""
        result = parse_whale_trade(_VALID_TRADE, _ADDRESS, _COLLECTED_AT)
        assert result is not None
        assert result.whale_address == "0xwhale"
        assert result.transaction_hash == "0xabc123"
        assert result.side == "BUY"
        assert result.asset_id == "token_001"
        assert result.condition_id == "cond_001"
        assert result.size == 100.5
        assert result.price == 0.65
        assert result.slug == "will-btc-hit-100k"
        assert result.outcome == "Yes"
        assert result.collected_at == _COLLECTED_AT

    def test_camel_case_fields(self) -> None:
        """Handle camelCase field variants from Gamma API."""
        trade: dict[str, Any] = {
            "transactionHash": "0xdef",
            "side": "SELL",
            "assetId": "token_camel",
            "conditionId": "cond_camel",
            "size": 50,
            "price": 0.45,
            "timestamp": 1710000000,
            "title": "Some Market",
            "market_slug": "some-market",
            "outcome": "No",
            "outcomeIndex": 1,
        }
        result = parse_whale_trade(trade, _ADDRESS, _COLLECTED_AT)
        assert result is not None
        assert result.asset_id == "token_camel"
        assert result.condition_id == "cond_camel"
        assert result.slug == "some-market"
        assert result.outcome_index == 1

    def test_missing_transaction_hash_returns_none(self) -> None:
        """Return None when required transactionHash is missing."""
        trade = {k: v for k, v in _VALID_TRADE.items() if k != "transactionHash"}
        result = parse_whale_trade(trade, _ADDRESS, _COLLECTED_AT)
        assert result is None

    def test_address_lowered(self) -> None:
        """Normalise whale address to lowercase."""
        result = parse_whale_trade(_VALID_TRADE, "0xABC123", _COLLECTED_AT)
        assert result is not None
        assert result.whale_address == "0xabc123"

    def test_missing_optional_fields_default(self) -> None:
        """Use defaults for optional fields like side, outcome."""
        trade: dict[str, Any] = {"transactionHash": "0xmin", "timestamp": 1710000000}
        result = parse_whale_trade(trade, _ADDRESS, _COLLECTED_AT)
        assert result is not None
        assert result.side == ""
        assert result.outcome == ""
        assert result.size == 0
        assert result.price == 0
