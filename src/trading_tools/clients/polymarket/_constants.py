"""Shared constants for Polymarket client modules.

Re-export HTTP status codes from the central module for backwards
compatibility with existing imports.
"""

from trading_tools.clients._http_status import HTTP_BAD_REQUEST

__all__ = ["HTTP_BAD_REQUEST"]
