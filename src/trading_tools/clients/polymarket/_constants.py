"""Shared constants for Polymarket client modules.

Re-export HTTP status codes from the central module and define
blockchain addresses used by multiple sub-modules.
"""

from trading_tools.clients._http_status import HTTP_BAD_REQUEST

USDC_E_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
"""Polygon USDC.e (bridged USDC) token contract address."""

__all__ = ["HTTP_BAD_REQUEST", "USDC_E_ADDRESS"]
