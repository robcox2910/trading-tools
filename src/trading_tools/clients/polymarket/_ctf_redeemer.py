"""Redeem winning conditional tokens via the Polymarket ProxyWalletFactory.

Call ``redeemPositions`` on the Gnosis Conditional Token Framework (CTF)
contract through the Polymarket ProxyWalletFactory's ``proxy()`` function.
This allows proxy-wallet users to redeem resolved market positions and
recover USDC.e collateral without Builder API access — only a tiny amount
of POL for gas is required.

The ProxyWalletFactory routes calls based on ``msg.sender``, so the
signing EOA must be the owner of the proxy wallet.
"""

import logging
from typing import Any

from web3 import Web3
from web3.types import Nonce, TxParams, TxReceipt, Wei

from trading_tools.clients.polymarket.exceptions import PolymarketAPIError

logger = logging.getLogger(__name__)

# Polygon contract addresses
_CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
_USDC_E_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
_PROXY_WALLET_FACTORY = "0xaB45c5A4B0c941a2F231C04C3f49182e1A254052"
_PARENT_COLLECTION_ID = b"\x00" * 32

# Redeem both YES (index 1) and NO (index 2) outcomes
_INDEX_SETS = [1, 2]

# Minimum ABI for redeemPositions on the CTF contract
_CTF_REDEEM_ABI: list[dict[str, Any]] = [
    {
        "name": "redeemPositions",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "collateralToken", "type": "address"},
            {"name": "parentCollectionId", "type": "bytes32"},
            {"name": "conditionId", "type": "bytes32"},
            {"name": "indexSets", "type": "uint256[]"},
        ],
        "outputs": [],
    },
]

# ProxyWalletFactory proxy() ABI — routes calls through the caller's proxy wallet
_FACTORY_PROXY_ABI: list[dict[str, Any]] = [
    {
        "name": "proxy",
        "type": "function",
        "stateMutability": "payable",
        "inputs": [
            {
                "name": "calls",
                "type": "tuple[]",
                "components": [
                    {"name": "typeCode", "type": "uint8"},
                    {"name": "to", "type": "address"},
                    {"name": "value", "type": "uint256"},
                    {"name": "data", "type": "bytes"},
                ],
            },
        ],
        "outputs": [{"name": "", "type": "bytes[]"}],
    },
]

_CALL_TYPE_CODE = 1  # CALL (not DELEGATECALL)
_DEFAULT_GAS = 300_000
_TX_RECEIPT_TIMEOUT = 120
_GAS_PRICE_MULTIPLIER = 1.25  # 25% above estimated to ensure inclusion


def _encode_redeem_calldata(condition_id: str) -> bytes:
    """Encode the ``redeemPositions`` function call for the CTF contract.

    Args:
        condition_id: Market condition ID as a hex string (with or without ``0x``).

    Returns:
        ABI-encoded calldata bytes.

    """
    w3 = Web3()
    ctf = w3.eth.contract(abi=_CTF_REDEEM_ABI)
    cid_hex = condition_id if condition_id.startswith("0x") else f"0x{condition_id}"
    cid_bytes = bytes.fromhex(cid_hex[2:].zfill(64))

    return ctf.encode_abi(  # type: ignore[no-any-return]
        "redeemPositions",
        [
            Web3.to_checksum_address(_USDC_E_ADDRESS),
            _PARENT_COLLECTION_ID,
            cid_bytes,
            _INDEX_SETS,
        ],
    )


def redeem_positions(
    rpc_url: str,
    private_key: str,
    condition_ids: list[str],
    *,
    gas: int = _DEFAULT_GAS,
) -> list[TxReceipt]:
    """Redeem winning positions for resolved markets via the proxy wallet.

    Submit one on-chain transaction per condition ID through the
    ProxyWalletFactory's ``proxy()`` function. Each transaction calls
    ``redeemPositions`` on the CTF contract, burning conditional tokens
    and recovering USDC.e collateral.

    Use the network's recommended gas price (with a 25% buffer) instead
    of a static value to avoid stale-gas failures on Polygon.

    Require POL in the signing EOA to pay for gas (typically < $0.01 per
    redemption on Polygon).

    Args:
        rpc_url: Polygon JSON-RPC endpoint URL.
        private_key: Hex-encoded private key of the proxy wallet owner.
        condition_ids: List of resolved market condition IDs to redeem.
        gas: Gas limit per transaction.

    Returns:
        List of transaction receipts for successful redemptions.
        Failed redemptions are logged and skipped so that remaining
        condition IDs are still attempted.

    Raises:
        PolymarketAPIError: When the RPC connection cannot be established.

    """
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    if not w3.is_connected():
        raise PolymarketAPIError(
            msg=f"Cannot connect to Polygon RPC at {rpc_url}",
            status_code=0,
        )

    account = w3.eth.account.from_key(private_key)
    factory = w3.eth.contract(
        address=Web3.to_checksum_address(_PROXY_WALLET_FACTORY),
        abi=_FACTORY_PROXY_ABI,
    )

    receipts: list[TxReceipt] = []
    nonce = w3.eth.get_transaction_count(account.address, "pending")

    for cid in condition_ids:
        redeem_data = _encode_redeem_calldata(cid)
        proxy_call = (
            _CALL_TYPE_CODE,
            Web3.to_checksum_address(_CTF_ADDRESS),
            0,
            redeem_data,
        )

        try:
            base_gas_price = w3.eth.gas_price
            boosted_gas_price = Wei(int(base_gas_price * _GAS_PRICE_MULTIPLIER))
            tx_params: TxParams = {
                "from": account.address,
                "gas": gas,
                "gasPrice": boosted_gas_price,
                "nonce": Nonce(nonce),
            }
            tx = factory.functions.proxy([proxy_call]).build_transaction(tx_params)
            signed = w3.eth.account.sign_transaction(tx, private_key=private_key)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=_TX_RECEIPT_TIMEOUT)
            receipts.append(receipt)

            status = "SUCCESS" if receipt["status"] == 1 else "FAILED"
            logger.info(
                "Redeemed %s: %s (gas used: %d, tx: %s)",
                cid[:20],
                status,
                receipt["gasUsed"],
                tx_hash.hex(),
            )
            nonce = Nonce(nonce + 1)
        except Exception:
            logger.warning("Failed to redeem %s", cid[:20], exc_info=True)

    return receipts
