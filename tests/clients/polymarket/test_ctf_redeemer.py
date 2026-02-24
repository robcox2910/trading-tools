"""Tests for the CTF position redeemer module."""

from unittest.mock import MagicMock, patch

import pytest

from trading_tools.clients.polymarket import _ctf_redeemer
from trading_tools.clients.polymarket.exceptions import PolymarketAPIError

_CONDITION_ID = "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
_PRIVATE_KEY = "0xdeadbeef" * 8
_RPC_URL = "https://polygon-rpc.example.com"
_MIN_CALLDATA_LEN = 4  # ABI function selector is 4 bytes


class TestEncodeRedeemCalldata:
    """Test the calldata encoding for redeemPositions."""

    def test_encodes_valid_condition_id(self) -> None:
        """Encode a valid condition ID into ABI calldata."""
        result = _ctf_redeemer._encode_redeem_calldata(_CONDITION_ID)
        assert len(result) > _MIN_CALLDATA_LEN

    def test_handles_condition_id_without_prefix(self) -> None:
        """Accept condition IDs without the 0x prefix."""
        cid_no_prefix = _CONDITION_ID[2:]
        result = _ctf_redeemer._encode_redeem_calldata(cid_no_prefix)
        assert len(result) > _MIN_CALLDATA_LEN


class TestRedeemPositions:
    """Test the redeem_positions function."""

    def test_raises_on_connection_failure(self) -> None:
        """Raise PolymarketAPIError when the RPC is unreachable."""
        with patch("trading_tools.clients.polymarket._ctf_redeemer.Web3") as mock_web3:
            mock_instance = MagicMock()
            mock_instance.is_connected.return_value = False
            mock_web3.return_value = mock_instance
            mock_web3.HTTPProvider = MagicMock()

            with pytest.raises(PolymarketAPIError, match="Cannot connect"):
                _ctf_redeemer.redeem_positions(
                    _RPC_URL,
                    _PRIVATE_KEY,
                    [_CONDITION_ID],
                )

    def test_returns_empty_for_no_condition_ids(self) -> None:
        """Return empty list when no condition IDs are provided."""
        with patch("trading_tools.clients.polymarket._ctf_redeemer.Web3") as mock_web3:
            mock_instance = MagicMock()
            mock_instance.is_connected.return_value = True
            mock_web3.return_value = mock_instance
            mock_web3.HTTPProvider = MagicMock()

            result = _ctf_redeemer.redeem_positions(
                _RPC_URL,
                _PRIVATE_KEY,
                [],
            )
            assert result == []

    def test_raises_on_transaction_failure(self) -> None:
        """Raise PolymarketAPIError when a transaction fails."""
        with patch("trading_tools.clients.polymarket._ctf_redeemer.Web3") as mock_web3:
            mock_instance = MagicMock()
            mock_instance.is_connected.return_value = True
            mock_web3.return_value = mock_instance
            mock_web3.HTTPProvider = MagicMock()

            def _checksum(addr: str) -> str:
                return addr

            mock_web3.to_checksum_address = _checksum

            # Mock contract interaction to raise
            mock_contract = MagicMock()
            mock_instance.eth.contract.return_value = mock_contract
            mock_instance.eth.get_transaction_count.return_value = 0
            mock_contract.functions.proxy.return_value.build_transaction.side_effect = RuntimeError(
                "gas estimation failed"
            )

            with pytest.raises(PolymarketAPIError, match="Failed to redeem"):
                _ctf_redeemer.redeem_positions(
                    _RPC_URL,
                    _PRIVATE_KEY,
                    [_CONDITION_ID],
                )
